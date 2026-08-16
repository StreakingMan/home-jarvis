#!/usr/bin/env python3
"""训练中文自定义唤醒词，直接导出 openWakeWord 能用的 .tflite。

## 为什么不走官方训练脚本

`openwakeword/train.py` 的导出链是 torch → onnx → **onnx_tf** → TF → tflite。
`onnx_tf` 已归档，跟现代 TF/protobuf 冲突，是个纯粹的坑。而且它的正样本生成
依赖 piper-sample-generator（英文 LibriTTS），中文用不上。

所以这里绕开它：**直接用 Keras 训分类头再导出 tflite**。可行的前提是
openWakeWord 的模型契约很简单（实测自带模型的签名）：

```
melspectrogram.tflite   [1,N]      → [1,1,1,32]     音频 → 梅尔谱
embedding_model.tflite  [1,76,32,1]→ [1,1,1,96]     梅尔谱 → 96 维嵌入
<唤醒词>.tflite          [1,16,96]  → [1,1]          16 帧嵌入 → 概率
```

前两级是**冻结的公共特征提取器**，所有唤醒词共用；要训的只有第三级那个小分类头。
这也是为什么训练成本低到 CPU 就够。

## 数据

- 正样本：`gen_wakeword_clips.py` 用 edge-tts 的 14 个中文音色合成的「你好曼波」
- 对抗负样本：同上，音近/部分重叠的短语（「你好」「曼波」「慢波」…）
- 大规模负样本：HF `davidscripka/openwakeword_features` 的 ACAV100M 2000 小时
  预计算特征（16.5GB，**已经在同一个嵌入空间里**，不用自己算）
- 增强：MIT 房间脉冲响应做混响 + 增益抖动

⚠️ 负样本的量级决定误唤醒率。只喂正样本和对抗负样本，模型会在电视声、
聊天声里频繁误触发 —— 那 16.5GB 才是压住误唤醒的主力。

用法：
    python scripts/train_wakeword.py --steps features   只算特征（可断点续）
    python scripts/train_wakeword.py --steps train      训练 + 导出
    python scripts/train_wakeword.py                    全部
"""
import argparse
import glob
import os
import random
import sys

import numpy as np

HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, "apps/wakeword/data")
FEAT = os.path.join(HOME, "apps/wakeword/features")
RIR = os.path.join(HOME, "apps/wakeword/rir")
OUT = os.path.join(HOME, "apps/wakeword/models")
MODELS = glob.glob(os.path.join(
    HOME, "miniconda3/envs/*/lib/python3.11/site-packages/wyoming_openwakeword/models"))

WINDOW = 16          # 分类头看 16 帧嵌入
EMB = 96
SR = 16000


def feature_extractor():
    """复用 openWakeWord 自带的 melspec + embedding 两级 tflite。

    ⚠️ 解释器有两个来源：运行服务的 `wakeword` 环境里是 `tflite_runtime`，
    训练用的 `wwtrain` 环境里没有它但有完整 TensorFlow。两边接口一致，
    所以这里做回退 —— 免得为了跑训练去污染正在提供服务的那个环境。
    """
    try:
        import tflite_runtime.interpreter as tflite
    except ImportError:
        from tensorflow import lite as tflite
    d = MODELS[0]

    def load(name, shape=None):
        it = tflite.Interpreter(model_path=os.path.join(d, name), num_threads=4)
        if shape is not None:
            it.resize_tensor_input(it.get_input_details()[0]["index"], shape)
        it.allocate_tensors()
        return it

    return load, d


def embed(audio, mel_it, emb_it):
    """16k 单声道（int16 数值域的 float32）→ [T,96] 嵌入序列

    ⚠️ 梅尔谱的输出轴序不能靠猜。上游 `openwakeword/utils.py` 用的是
    `np.squeeze(outputs[0])` 得到 [T,32]；我一开始按 `[0,:,:,0]` 切，
    切出来是 [1,T]，于是窗口数恒为 0、嵌入是空的 —— 而且不报错。
    """
    mi = mel_it.get_input_details()[0]
    mel_it.set_tensor(mi["index"], audio[None, :].astype(np.float32))
    mel_it.invoke()
    mel = np.squeeze(mel_it.get_tensor(mel_it.get_output_details()[0]["index"]))
    # openWakeWord 的固定变换：让 ONNX 版梅尔谱贴近 Google 原始 TF 实现
    mel = mel / 10.0 + 2.0

    win = [mel[i:i + 76] for i in range(0, mel.shape[0], 8)]
    win = [w for w in win if w.shape[0] == 76]
    if not win:
        return np.zeros((0, EMB), np.float32)
    batch = np.array(win, dtype=np.float32)[..., None]
    ei = emb_it.get_input_details()[0]
    emb_it.resize_tensor_input(ei["index"], batch.shape)
    emb_it.allocate_tensors()
    emb_it.set_tensor(ei["index"], batch)
    emb_it.invoke()
    return np.squeeze(emb_it.get_tensor(emb_it.get_output_details()[0]["index"])).astype(np.float32)


def load_audio(path):
    import subprocess
    ff = os.path.join(os.path.dirname(sys.executable), "ffmpeg")
    ff = ff if os.path.exists(ff) else "ffmpeg"
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)


def augment(a, rirs, n):
    """混响 + 增益 + 前后留白抖动。留白很重要 —— 真实说话前后总有静音"""
    import scipy.signal
    out = []
    for _ in range(n):
        x = a.copy()
        if rirs and random.random() < 0.7:
            r = load_audio(random.choice(rirs))
            if len(r) > 1:
                x = scipy.signal.fftconvolve(x, r / (np.abs(r).max() + 1e-9))[:len(x) + 2000]
        x *= 10 ** (random.uniform(-12, 3) / 20)
        pad_l = int(random.uniform(0.1, 0.8) * SR)
        pad_r = int(random.uniform(0.2, 1.0) * SR)
        x = np.concatenate([np.zeros(pad_l, np.float32), x, np.zeros(pad_r, np.float32)])
        x += np.random.randn(len(x)).astype(np.float32) * random.uniform(0, 60)
        out.append(np.clip(x, -32768, 32767))
    return out


def windows(seq):
    """[T,96] → 所有长度 16 的滑窗 [N,16,96]"""
    if len(seq) < WINDOW:
        return np.zeros((0, WINDOW, EMB), np.float32)
    return np.stack([seq[i:i + WINDOW] for i in range(len(seq) - WINDOW + 1)])


def build_features(a):
    load, d = feature_extractor()
    mel = load("melspectrogram.tflite", [1, SR * 4])
    emb = load("embedding_model.tflite")
    rirs = sorted(glob.glob(os.path.join(RIR, "**", "*.wav"), recursive=True))
    print(f"  混响脉冲 {len(rirs)} 个")
    os.makedirs(FEAT, exist_ok=True)

    for kind, aug_n in (("positive", a.aug_pos), ("adversarial", a.aug_neg)):
        files = sorted(glob.glob(os.path.join(DATA, kind, "*.mp3")))
        if not files:
            print(f"  ⚠️ {kind} 目录为空，先跑 gen_wakeword_clips.py")
            continue
        chunks = []
        for i, f in enumerate(files):
            base = load_audio(f)
            for x in augment(base, rirs, aug_n):
                x = x[:SR * 4]
                x = np.pad(x, (0, SR * 4 - len(x))) if len(x) < SR * 4 else x
                w = windows(embed(x, mel, emb))
                if len(w):
                    chunks.append(w)
            if (i + 1) % 50 == 0:
                print(f"    {kind} {i+1}/{len(files)}", flush=True)
        arr = np.concatenate(chunks) if chunks else np.zeros((0, WINDOW, EMB), np.float32)
        np.save(os.path.join(FEAT, f"{kind}_features.npy"), arr)
        print(f"  {kind}: {arr.shape} → {kind}_features.npy")


def train(a):
    import tensorflow as tf
    pos = np.load(os.path.join(FEAT, "positive_features.npy"))
    adv = np.load(os.path.join(FEAT, "adversarial_features.npy"))
    big = os.path.join(FEAT, "openwakeword_features_ACAV100M_2000_hrs_16bit.npy")
    neg_big = np.load(big, mmap_mode="r") if os.path.exists(big) else None
    if neg_big is not None:
        take = min(a.neg_samples, len(neg_big))
        idx = np.random.default_rng(0).choice(len(neg_big), take, replace=False)
        neg_big = np.asarray(neg_big[np.sort(idx)], dtype=np.float32)
        print(f"  大规模负样本抽 {take} / {len(np.load(big, mmap_mode='r'))}")
    else:
        print("  ⚠️ 没有大规模负样本特征，误唤醒率会明显偏高")
        neg_big = np.zeros((0, WINDOW, EMB), np.float32)

    # 对抗负样本加权重复 —— 它们数量少但最关键
    X = np.concatenate([pos, np.repeat(adv, a.adv_weight, axis=0), neg_big])
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(adv) * a.adv_weight), np.zeros(len(neg_big))])
    print(f"  训练集 {X.shape}  正 {int(y.sum())} / 负 {int((1-y).sum())}")

    p = np.random.default_rng(1).permutation(len(X))
    X, y = X[p], y[p]
    n_val = int(len(X) * 0.1)
    Xv, yv, X, y = X[:n_val], y[:n_val], X[n_val:], y[n_val:]

    m = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(WINDOW, EMB)),
        tf.keras.layers.Flatten(),
        tf.keras.layers.Dense(128, activation="relu"),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(64, activation="relu"),
        tf.keras.layers.Dense(1, activation="sigmoid"),
    ])
    m.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="binary_crossentropy",
              metrics=[tf.keras.metrics.Recall(name="recall"),
                       tf.keras.metrics.Precision(name="precision")])
    m.fit(X, y, validation_data=(Xv, yv), epochs=a.epochs, batch_size=1024,
          class_weight={0: 1.0, 1: a.pos_weight}, verbose=2)

    # 误唤醒率：拿官方验证集（纯负样本）过一遍
    vpath = os.path.join(FEAT, "validation_set_features.npy")
    if os.path.exists(vpath):
        V = np.load(vpath, mmap_mode="r")
        V = np.asarray(V[:min(200000, len(V))], dtype=np.float32)
        s = m.predict(V, batch_size=4096, verbose=0).ravel()
        print("\n  误唤醒（官方验证集，纯负样本）：")
        for th in (0.3, 0.5, 0.7, 0.9):
            print(f"    阈值 {th}: 触发率 {(s>th).mean()*100:.4f}%")
    print("\n  正样本召回：")
    sp = m.predict(pos, batch_size=4096, verbose=0).ravel()
    for th in (0.3, 0.5, 0.7, 0.9):
        print(f"    阈值 {th}: 召回 {(sp>th).mean()*100:.1f}%")

    os.makedirs(OUT, exist_ok=True)
    conv = tf.lite.TFLiteConverter.from_keras_model(m)
    tfl = conv.convert()
    dst = os.path.join(OUT, f"{a.name}.tflite")
    open(dst, "wb").write(tfl)
    print(f"\n  已导出 {dst}（{len(tfl)/1024:.0f} KB）")
    print(f"  部署：唤醒词名就是文件名 —— run_satellite.sh 里把 wake-word-name 改成 {a.name}")


ap = argparse.ArgumentParser()
ap.add_argument("--steps", default="all", choices=["all", "features", "train"])
ap.add_argument("--name", default="ni_hao_manbo")
ap.add_argument("--aug-pos", type=int, default=25, help="每条正样本增强几份")
ap.add_argument("--aug-neg", type=int, default=4)
ap.add_argument("--neg-samples", type=int, default=2_000_000, help="从大负样本集抽多少窗口")
ap.add_argument("--adv-weight", type=int, default=20, help="对抗负样本重复几遍")
ap.add_argument("--pos-weight", type=float, default=8.0)
ap.add_argument("--epochs", type=int, default=25)
_a = ap.parse_args()
if _a.steps in ("all", "features"):
    build_features(_a)
if _a.steps in ("all", "train"):
    train(_a)
