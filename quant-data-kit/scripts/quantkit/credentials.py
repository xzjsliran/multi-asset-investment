"""私有凭证留在用户配置目录；项目只读取是否可用，不导出凭证。"""
import getpass
import json
import os
from pathlib import Path

ENV_NAMES = {"tushare":"TUSHARE_TOKEN", "ifind":"IFIND_API_KEY"}


def credential_path():
    return Path.home() / ".config" / "multi-assets" / "credentials.json"


def read_private():
    path = credential_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise ValueError("本机凭证配置无法读取，请在本地重新配置。") from None


def get_credential(provider):
    if provider not in ENV_NAMES:
        raise ValueError("凭证类型为tushare或ifind。")
    value = os.environ.get(ENV_NAMES[provider], "").strip() or str(read_private().get(provider, "")).strip()
    if value:
        return value
    if provider == "tushare":
        try:
            import tushare as ts
            return ts.get_token() or ""
        except Exception:
            pass
    return ""


def save_credential(provider, value):
    if provider not in ENV_NAMES or not isinstance(value, str) or not value.strip():
        raise ValueError("凭证类型无效或内容为空。")
    path = credential_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old = read_private()
    old[provider] = value.strip()
    # 临时文件也只在私有目录内；使用0600创建，避免权限收紧前的暴露窗口。
    pending = path.with_suffix(".pending")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False)
        os.replace(pending, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if pending.exists():
            pending.unlink()


def status():
    return {name:{"configured":bool(get_credential(name)), "tested":False} for name in ENV_NAMES}


def configure(provider):
    value = getpass.getpass("在本地终端粘贴" + provider + "密钥（不回显；留空保留现有配置）：").strip()
    if not value:
        print("未修改现有配置。")
        return
    save_credential(provider, value)
    print("凭证已保存到本机私有配置目录，未写入项目。")
