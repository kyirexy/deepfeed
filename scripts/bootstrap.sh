#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ ! -f .env ]; then
  cp .env.example .env
fi

SECRET=$(grep '^SEARXNG_SECRET=' .env | cut -d= -f2- || true)
if [ -z "$SECRET" ] || [ "$SECRET" = "replace-with-a-random-secret" ]; then
  if command -v openssl >/dev/null 2>&1; then
    SECRET=$(openssl rand -hex 32)
  else
    SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')
  fi
  python - "$SECRET" <<'PY'
from pathlib import Path
import sys
p=Path('.env')
lines=p.read_text('utf-8').splitlines()
out=[]
found=False
for line in lines:
    if line.startswith('SEARXNG_SECRET='):
        out.append('SEARXNG_SECRET='+sys.argv[1]); found=True
    else:
        out.append(line)
if not found: out.append('SEARXNG_SECRET='+sys.argv[1])
p.write_text('\n'.join(out)+'\n','utf-8')
PY
fi

python - "$SECRET" <<'PY'
from pathlib import Path
import sys
src=Path('infra/searxng/settings.yml').read_text('utf-8')
Path('infra/searxng/settings.local.yml').write_text(src.replace('ultrasecretkey',sys.argv[1]),'utf-8')
PY

echo "配置已生成。运行：docker compose up -d"
