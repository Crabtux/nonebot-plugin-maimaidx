from pydantic import BaseModel


class DeviceAuthorization(BaseModel):
    """发起绑定后水鱼账号返回的授权信息"""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int = 5


class AccessToken(BaseModel):
    """代用户访问的令牌，没有 `refresh_token`，过期后重新换取"""

    access_token: str
    token_type: str
    expires_in: int
    scope: str
