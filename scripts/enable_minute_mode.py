from pathlib import Path

path = Path("index.html")
text = path.read_text("utf-8")
original = text

text = text.replace(
    "https://raw.githubusercontent.com/kyirexy/deepfeed/main/data/live.json",
    "https://raw.githubusercontent.com/kyirexy/deepfeed/live-data/live.json",
)
text = text.replace(
    "const DATA_URL = location.protocol === 'file:' ? RAW_DATA_URL : '/api/live';",
    "const DATA_URL = (location.protocol === 'file:' || location.hostname.endsWith('github.io')) ? RAW_DATA_URL : '/api/live';",
)
text = text.replace("state.timer=setInterval(load,5*60*1000);", "state.timer=setInterval(load,60*1000);")
text = text.replace("每 5 分钟", "每 1 分钟")
text = text.replace("每5分钟", "每1分钟")
text = text.replace("5 分钟自动刷新", "1 分钟自动刷新")
text = text.replace("5分钟自动刷新", "1分钟自动刷新")

if text != original:
    path.write_text(text, "utf-8")
    print("index.html minute mode enabled")
else:
    print("index.html already in minute mode")
