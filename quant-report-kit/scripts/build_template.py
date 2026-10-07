#!/usr/bin/env python3
"""维护页面时使用；普通用户生成报告不需要Node或Data插件。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--data-plugin", required=True)
    p.add_argument("--workdir", required=True, help="新的模板构建目录；Office临时文件请遵循工作区约定")
    args = p.parse_args()
    kit = Path(__file__).resolve().parents[1]
    work = Path(args.workdir).resolve()
    if work.exists():
        raise ValueError("模板构建目录应为新目录，避免修改已有报告应用。")
    work.mkdir(parents=True)
    snapshot = work/"empty-snapshot.json"
    snapshot.write_text(json.dumps({"surface":"report", "id":"quant-report-template-v1", "title":"量化策略研究报告",
        "status":"reviewed", "buildStatus":"complete", "filters":[], "queries":{}, "quantReport":None}, ensure_ascii=False), encoding="utf-8")
    plugin = Path(args.data_plugin).resolve()
    app = work/"app"
    # 新建可复用模板，不把作者的桌面会话编号带进使用者的插件。
    env = dict(os.environ)
    env.pop("CODEX_SESSION_ID", None); env.pop("CODEX_THREAD_ID", None)
    def command(script, *parts):
        return subprocess.run([args.node,str(plugin/"scripts"/script),*map(str,parts)], env=env, check=True, capture_output=True, text=True)
    command("prepare-data-app.mjs", "--surface","report","--output",app,"--snapshot",snapshot)
    for name in ["ReportContent.jsx","report.css"]:
        shutil.copy2(kit/"assets"/name,app/"src/content/report"/name)
    # prepare给新应用标记creating；模板内容已就绪后按正常工作流完成构建。
    data_file=app/"src/data.json"
    d=json.loads(data_file.read_text());d["buildStatus"]="complete"
    data_file.write_text(json.dumps(d,ensure_ascii=False),encoding="utf-8")
    build=command("data-app.mjs","build","--project-dir",app,"--separate-data")
    exported=app/".data-app-offline/exports/template.html"
    command("data-app.mjs","export-offline","--project-dir",app,"--output",exported)
    raw=exported.read_text(encoding="utf-8")
    pattern=r'(<script\s+type="application/json"\s+id="data-app-reviewed-snapshot">)(.*?)(</script>)'
    match=re.search(pattern,raw,re.S)
    if not match or "data-app-snapshot-chunk>" in raw or 'name="data-app-local-thread"' in raw:
        raise ValueError("导出的空模板格式不符，未写入kit。")
    # 只把惰性JSON数据换成占位符，编译后的共享组件和启动代码原样保留。
    template=raw[:match.start(2)]+"__QUANT_REPORT_SNAPSHOT__"+raw[match.end(2):]
    target=kit/"assets/report-template.html"
    target.write_text(template,encoding="utf-8")
    info={"template_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"template_bytes":target.stat().st_size,
          "runtime":"Data app shared offline runtime", "contains_downloaded_data":False,
          "sources":{name:hashlib.sha256((kit/"assets"/name).read_bytes()).hexdigest() for name in ["ReportContent.jsx","report.css"]},
          "build":json.loads(build.stdout.strip().splitlines()[-1])}
    # 仅保留可重建的版本/指纹；作者机器上的临时路径不放进套装。
    info["build"]={k:v for k,v in info["build"].items() if k in ["apiVersion","runtimeSha256","compilerSha256","buildKind"]}
    (kit/"assets/template-info.json").write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(info,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
