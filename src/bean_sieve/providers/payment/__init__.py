"""Payment platform providers (Alipay, WeChat Pay, etc.)."""

from .alipay import AlipayProvider
from .jd import JDProvider
from .wechat import WechatProvider

__all__ = ["AlipayProvider", "JDProvider", "WechatProvider"]
