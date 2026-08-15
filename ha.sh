#!/bin/zsh
# HA REST API 快捷工具
# 用法:
#   ./ha.sh states                     # 列出所有实体 (entity_id + state)
#   ./ha.sh state <entity_id>          # 查看单个实体详情
#   ./ha.sh call <domain> <service> '<json>'   # 调用服务
#       例: ./ha.sh call light turn_on '{"entity_id":"light.living_room"}'
#   ./ha.sh services                   # 列出所有可用服务
#   ./ha.sh get <api_path>             # 任意 GET, 如 ./ha.sh get /api/config

set -e
cd "$(dirname "$0")"
source .env

_get() { curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL$1"; }
_post() { curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" -d "$2" "$HA_URL$1"; }

case "$1" in
  states)   _get /api/states | jq -r '.[] | "\(.entity_id)\t\(.state)"' | sort ;;
  state)    _get "/api/states/$2" | jq . ;;
  call)     _post "/api/services/$2/$3" "${4:-{}}" | jq . ;;
  services) _get /api/services | jq -r '.[] | .domain as $d | .services | keys[] | "\($d).\(.)"' | sort ;;
  get)      _get "$2" | jq . ;;
  *)        grep '^#' "$0" | sed 's/^# \{0,1\}//' ;;
esac
