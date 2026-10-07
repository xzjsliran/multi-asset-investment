"""可选iFinD MCP查询：限定调用次数、缓存证据、独立本机凭证。"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time

import requests

from .common import now, write_json
from .credentials import get_credential

BASE = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-"
SERVICES = {s:BASE+n+"-mcp" for s,n in [("stock","stock"),("fund","fund"),("edb","edb"),
    ("news","news"),("bond","bond"),("global_stock","global-stock"),("index","index"),("future","futures")]}


def decode_rpc(response):
    """兼容普通JSON与MCP的SSE响应，不执行返回的文本。"""
    try:
        return response.json()
    except ValueError:
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            lines = [x[5:].lstrip() for x in event.splitlines() if x.startswith("data:")]
            if lines:
                try:
                    body = json.loads("\n".join(lines))
                    if "result" in body or "error" in body:
                        return body
                except ValueError:
                    pass
        raise RuntimeError("iFinD返回了无法识别的响应，未自动重试。") from None


class IfindClient:
    def __init__(self, work, max_calls=4, token=None):
        self.token = token or get_credential("ifind")
        if not self.token:
            raise ValueError("未配置iFinD密钥；可继续使用免费数据源。")
        if not isinstance(max_calls, int) or not 1 <= max_calls <= 100:
            raise ValueError("单个查询工作目录预算须为1—100次；日常试取默认4次。")
        self.work = Path(work).resolve()
        project = Path(__file__).resolve().parents[3]
        for kit in project.glob("quant-*-kit"):
            if self.work == kit or kit in self.work.parents:
                raise ValueError("iFinD查询结果和调用记录须保存在kit外。")
        self.work.mkdir(parents=True, exist_ok=True)
        self.max_calls, self.sessions, self.schemas = max_calls, {}, {}
        self.identity = hashlib.sha256(self.token.encode()).hexdigest()[:16]

    def _redact(self, obj):
        if isinstance(obj, dict):
            return {k:("[redacted]" if k.lower() in {"authorization","auth_token","api_key","access_token"} else self._redact(v)) for k,v in obj.items()}
        if isinstance(obj, list):
            return [self._redact(x) for x in obj]
        return obj.replace(self.token,"[redacted]") if isinstance(obj,str) else obj

    def _post(self, service, method, params=None):
        if service not in SERVICES:
            raise ValueError("未知iFinD服务。")
        headers = {"Authorization":self.token, "Content-Type":"application/json", "Accept":"application/json, text/event-stream"}
        if service in self.sessions:
            headers["Mcp-Session-Id"] = self.sessions[service]
        body = {"jsonrpc":"2.0", "method":method}
        if not method.startswith("notifications/"):
            body["id"] = time.time_ns()
        if params is not None:
            body["params"] = params
        try:
            response = requests.post(SERVICES[service], json=body, headers=headers, timeout=(10,50), allow_redirects=False)
        except requests.RequestException:
            # 不输出可能含HTTP头/参数的底层异常；超时也记作一次已尝试的工具调用。
            raise RuntimeError("iFinD连接失败或超时，未自动重试；请检查网络和证书。") from None
        if not 200 <= response.status_code < 300:
            raise RuntimeError("iFinD HTTP " + str(response.status_code) + "；未自动重试。")
        if method.startswith("notifications/"):
            return {}
        data = decode_rpc(response)
        if data.get("error"):
            raise RuntimeError("iFinD RPC错误：" + json.dumps(self._redact(data["error"]), ensure_ascii=False)[:400])
        if method == "initialize":
            session = response.headers.get("Mcp-Session-Id")
            self.sessions[service] = session or ""
        return data.get("result", {})

    def list_tools(self, service):
        if service not in self.sessions:
            self._post(service,"initialize", {"protocolVersion":"2025-03-26", "capabilities":{},
                       "clientInfo":{"name":"multi-asset-data-kit", "version":"1.0"}})
            self._post(service,"notifications/initialized")
        result = self._post(service,"tools/list",{})
        self.schemas[service] = {x["name"]:x for x in result.get("tools",[])}
        return self._redact(result)

    def call(self, service, tool, params):
        if not isinstance(params, dict):
            raise ValueError("查询参数需要JSON对象。")
        spec = {"service":service,"tool":tool,"params":params}
        raw = json.dumps(spec, sort_keys=True, allow_nan=False, ensure_ascii=False)
        if self.token in raw:
            raise ValueError("查询内容不得包含凭证。")
        key = hashlib.sha256((self.identity+raw).encode()).hexdigest()
        path = self.work/(key+".json")
        if path.exists():
            return {**json.loads(path.read_text(encoding="utf-8")), "cached":True}
        if service not in self.schemas:
            self.list_tools(service)
        if tool not in self.schemas[service]:
            raise ValueError("当前密钥的工具清单不包含所需工具。")
        # 跨进程保留累计尝试次数，失败不自动退回预算，防止重复查询耗尽额度。
        lock = self.work/"budget.lock"
        try:
            fd = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError("该iFinD工作目录正在查询，请等待当前请求完成。") from None
        os.close(fd)
        try:
            ledger = self.work/"budget.json"
            log = json.loads(ledger.read_text()) if ledger.exists() else {"attempted_calls":0,"requests":[]}
            if log["attempted_calls"] >= self.max_calls:
                raise ValueError("本次iFinD查询已达到约定次数，停止调用。")
            log["attempted_calls"] += 1
            log["requests"].append({"key":key,"service":service,"tool":tool,"attempted_at":now()})
            write_json(ledger,log)
            try:
                result = self._post(service,"tools/call",{"name":tool,"arguments":params})
                record = {"ok":not result.get("isError",False),"result":self._redact(result)}
            except RuntimeError as exc:
                record = {"ok":False,"error":str(exc)}
            record.update(source="ifind_mcp",request=spec,fetched_at=now(),cached=False,
                          signal_eligible=False, note="查询原始证据；日期、单位、复权及历史版本经核对后方可转换为策略输入。")
            write_json(path, record)
            return record
        finally:
            lock.unlink()
