"""Human approval request contract."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApprovalAction(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_REVISION = "REQUEST_REVISION"
    REJECT = "REJECT"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor_id: str = Field(min_length=1, max_length=50)
    result_version: int = Field(ge=1)
    action: ApprovalAction
    approval_comment: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_comment(self) -> "ApprovalRequest":
        if self.action is ApprovalAction.REQUEST_REVISION:
            if not self.approval_comment or not self.approval_comment.strip():
                raise ValueError("REQUEST_REVISION에는 approval_comment가 필요합니다.")
        elif self.approval_comment is not None:
            raise ValueError("APPROVE와 REJECT에는 approval_comment를 보낼 수 없습니다.")
        return self
