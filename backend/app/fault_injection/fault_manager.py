"""Test Mode 장애를 한 Workflow 안에서 언제 주입할지 관리한다."""

from .fault_types import FaultType


class FaultManager:
    """한 Workflow에 설정된 장애 유형과 주입 여부를 관리한다.

    Workflow마다 별도 인스턴스를 만들어야 한다. ``_once`` 장애는 최초
    해당 단계에서 한 번만 주입하고, ``retry_limit_exceeded``는 재시도
    한도 초과를 확인할 수 있도록 호출될 때마다 주입한다.
    """

    def __init__(self, fault_type: FaultType | str | None = None) -> None:
        """요청된 장애 유형을 저장하고 주입 이력을 초기화한다."""
        self.fault_type = FaultType(fault_type) if fault_type is not None else None
        self._consumed = False

    def should_inject(self, fault_type: FaultType | str) -> bool:
        """현재 단계에서 지정된 장애를 주입해야 하면 참을 반환한다.

        실제 오류 발생이나 Planner 결과 변경은 호출하는 단계가 담당한다.
        이 메서드는 설정된 장애 유형과 주입 횟수만 판단한다.
        """
        candidate = FaultType(fault_type)

        if self.fault_type is not candidate:
            return False

        if candidate is FaultType.RETRY_LIMIT_EXCEEDED:
            return True

        if self._consumed:
            return False

        self._consumed = True
        return True
