# logic/trade_manager_base.py
from abc import ABC, abstractmethod

class TradeManagerBase(ABC):
    """
    TradeManager의 기본 인터페이스 역할을 하는 추상 클래스.
    모든 거래 관리자는 이 클래스를 상속받아 아래 메서드들을 구현해야 합니다.
    """
    @abstractmethod
    def set_leverage(self, coin, leverage):
        """레버리지를 설정합니다."""
        pass

    @abstractmethod
    def enter_short_position(self, coin):
        """숏 포지션에 진입합니다."""
        pass

    @abstractmethod
    def close_short_position(self, coin, amount):
        """숏 포지션을 청산합니다."""
        pass