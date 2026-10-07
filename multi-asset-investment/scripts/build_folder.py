"""生成独立插件文件夹，显式白名单复制；不打包数据、环境、结果或密钥。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

KITS = ["quant-data-kit","quant-strategy-kit","quant-report-kit","quant-rebalance-kit"]
SUFFIXES = {".py",".md",".json",".jsx",".css",".html",".txt",".yaml"}
DIRECTORIES = {"scripts","references","assets","templates","examples","tests","agents"}


def build(output):
    plugin = Path(__file__).resolve().parents[1]
    source = plugin if (plugin/KITS[0]).exists() else plugin.parent
    out = Path(output).resolve()
    if out == plugin or plugin in out.parents or any(out == source/k or source.joinpath(k) in out.parents for k in KITS):
        raise ValueError("输出须位于源模块以外的新目录。")
    if out.exists():
        raise ValueError("目标已存在，请使用新的插件文件夹。")
    files = []
    def collect(root, prefix=Path()):
        for f in root.rglob("*"):
            rel = f.relative_to(root)
            if f.is_symlink() or not f.is_file() or "__pycache__" in rel.parts:
                continue
            if len(rel.parts)>1 and rel.parts[0] not in DIRECTORIES:
                continue
            if f.suffix not in SUFFIXES and f.name != ".gitignore":
                continue
            if len(rel.parts)==1 and f.name not in {"SKILL.md","README.md","requirements.txt","requirements-extended.txt",".gitignore"}:
                continue
            if any(x in f.name.lower() for x in ["credentials.json","mcp_config.json",".env","token.json"]):
                continue
            files.append((f, prefix/rel))
    collect(plugin)
    for kit in KITS:
        collect(source/kit, Path(kit))
    # 不复制本机私有配置；并扫描当前已配置凭证，防止维护时误写进方法文件。
    import sys
    sys.path.insert(0,str(source/"quant-data-kit/scripts"))
    from quantkit.credentials import get_credential
    secrets = [get_credential(k).encode() for k in ["tushare","ifind"] if get_credential(k)]
    for f,rel in files:
        content=f.read_bytes()
        if any(key in content for key in secrets):
            raise ValueError("源文件包含本机凭证，已停止生成插件："+str(rel))
    manifest={}
    for f,rel in files:
        dst=out/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dst)
        manifest[str(rel)]=hashlib.sha256(dst.read_bytes()).hexdigest()
    (out/"plugin-manifest.json").write_text(json.dumps({"schema_version":1,"files":manifest,"contains_market_data":False,"contains_credentials":False},ensure_ascii=False,indent=2),encoding="utf-8")
    return {"directory":str(out),"files":len(manifest),"contains_market_data":False,"contains_credentials":False}

if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--out",required=True)
    print(json.dumps(build(p.parse_args().out),ensure_ascii=False))
