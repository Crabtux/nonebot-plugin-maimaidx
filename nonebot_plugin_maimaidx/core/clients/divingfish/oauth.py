"""水鱼查分器 OAuth。

BOT 只保管 `client_id` 与 `client_secret`，不保存任何用户令牌：

1. 用户发送「绑定水鱼」，BOT 换取一个 `user_code` 并把授权链接发给用户；
2. 用户在水鱼账号页面确认授权，绑定关系与授权范围都记录在水鱼服务端；
3. 之后每次查询，BOT 用应用凭据换取该用户 5 分钟有效的 `access_token`。

用户标识只以 `sha256("<client_id>:<QQ号>")` 的形式发送，水鱼服务端存的也是
这个摘要，因此 QQ 号不会离开 BOT。
"""

from hashlib import sha256
from time import monotonic

from httpx import Response

from ....config import dfconfig
from ..http import ApiClient
from .exceptions import (
    DivingFishNotAuthorizedError,
    DivingFishOAuthError,
    DivingFishTokenNotFoundError,
)
from .models import AccessToken, DeviceAuthorization

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
ON_BEHALF_OF_GRANT = "urn:diving-fish:params:oauth:grant-type:on-behalf-of"
REVOKE_URL = "/apps"

#: 提前一点过期，避免令牌在请求途中失效
EXPIRES_MARGIN = 30


def subject_ref(qqid: int) -> str:
    """用户标识摘要，水鱼服务端用它定位授权过的账号"""
    return sha256(f"{dfconfig.divingfish_client_id}:{qqid}".encode()).hexdigest()


def binding_label(qqid: int) -> str:
    """展示在授权页面上的绑定身份，用户凭它确认不是在给别人授权"""
    qq = str(qqid)
    if len(qq) <= 4:
        return f"QQ {qq}"
    return f"QQ {qq[:2]}{'*' * (len(qq) - 4)}{qq[-2:]}"


class TokenCache:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}

    def get(self, ref: str) -> str | None:
        cached = self._tokens.get(ref)
        if cached is None:
            return None
        token, expires_at = cached
        if expires_at <= monotonic():
            del self._tokens[ref]
            return None
        return token

    def set(self, ref: str, token: AccessToken) -> None:
        expires_at = monotonic() + max(token.expires_in - EXPIRES_MARGIN, 0)
        self._tokens[ref] = (token.access_token, expires_at)

    def discard(self, ref: str) -> None:
        self._tokens.pop(ref, None)


tokens = TokenCache()


class DivingFishOAuth(ApiClient):
    def __init__(self):
        super().__init__(base_url=dfconfig.divingfish_auth_url.rstrip("/"))
        self.client_id = dfconfig.divingfish_client_id
        self.client_secret = dfconfig.divingfish_client_secret

    async def device_authorization(self, qqid: int) -> DeviceAuthorization:
        """发起绑定，返回给用户点开的授权链接

        顺带丢掉缓存的令牌：绑定到另一个账号后，旧令牌仍会在缓存里存活到过期，
        那段时间查出来的还是上一个账号的成绩
        """
        tokens.discard(subject_ref(qqid))
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": dfconfig.divingfish_oauth_scope,
            "subject_ref": subject_ref(qqid),
            "binding_label": binding_label(qqid),
        }
        result = await self._request_data(
            "POST", "/oauth/device_authorization", data=data
        )
        return DeviceAuthorization.model_validate(result)

    async def fetch_token(self, qqid: int) -> AccessToken:
        """换取代该用户访问的令牌"""
        data = {
            "grant_type": ON_BEHALF_OF_GRANT,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "subject": f"ref:{subject_ref(qqid)}",
            "scope": dfconfig.divingfish_oauth_scope,
        }
        result = await self._request_data("POST", "/oauth/token", data=data)
        return AccessToken.model_validate(result)

    async def _request_data(self, method: str, endpoint: str, **kwargs) -> dict:
        return await self._request(method, endpoint, **kwargs)

    def _handle_error(self, resp: Response) -> None:
        if resp.status_code == 200:
            return
        if not dfconfig.oauth_enabled:
            raise DivingFishTokenNotFoundError

        error = ""
        try:
            error = resp.json().get("error", "")
        except ValueError:
            pass

        if error == "consent_required":
            raise DivingFishNotAuthorizedError
        raise DivingFishOAuthError


async def get_access_token(qqid: int, *, refresh: bool = False) -> str:
    """取该用户的令牌，命中缓存则直接复用

    Params:
        `qqid`: 用户QQ
        `refresh`: 丢弃缓存重新换取
    """
    ref = subject_ref(qqid)
    if refresh:
        tokens.discard(ref)
    elif token := tokens.get(ref):
        return token

    result = await DivingFishOAuth().fetch_token(qqid)
    tokens.set(ref, result)
    return result.access_token
