"""기획 수정과 기술 재시도의 횟수 제한을 계산한다."""

DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_REVISIONS = 2


def next_retry_count(current_count: int, max_retries: int = DEFAULT_MAX_RETRIES) -> int | None:
    """기술 재시도가 가능하면 다음 횟수를, 한도에 도달했으면 ``None``을 반환한다.

    ``current_count``는 현재 장애에 대해 이미 수행한 연속 재시도 횟수다.
    장애가 복구되면 Workflow Engine이 이 값을 0으로 초기화한다.
    """
    return _next_count(current_count, max_retries)


def next_revision_count(
    current_count: int, max_revisions: int = DEFAULT_MAX_REVISIONS
) -> int | None:
    """자동 기획 수정이 가능하면 다음 횟수를, 한도에 도달했으면 ``None``을 반환한다.

    운영자의 ``REQUEST_REVISION``은 자동 기획 수정 횟수에 포함하지 않는다.
    """
    return _next_count(current_count, max_revisions)


def _next_count(current_count: int, limit: int) -> int | None:
    """현재 횟수와 상한을 검사한 뒤 허용되는 다음 횟수를 계산한다."""
    if type(current_count) is not int or type(limit) is not int:
        raise TypeError("횟수와 한도는 정수여야 합니다.")
    if current_count < 0 or limit < 1:
        raise ValueError("횟수는 0 이상, 한도는 1 이상이어야 합니다.")
    if current_count >= limit:
        return None
    return current_count + 1
