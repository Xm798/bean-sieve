"""Smart-importer integration for ML-based account prediction."""

from pathlib import Path

from .types import MatchSource, Transaction


class SmartPredictor:
    """
    Account predictor using smart-importer.

    This is a stub implementation. Full implementation requires
    the smart-importer package to be installed.
    """

    def __init__(
        self,
        ledger_path: Path,
        min_confidence: float = 0.8,
    ):
        self.ledger_path = ledger_path
        self.min_confidence = min_confidence
        self._model = None
        self._available = False
        self._check_availability()

    def _check_availability(self) -> None:
        """Check if smart-importer is available."""
        try:
            import smart_importer  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False

    @property
    def is_available(self) -> bool:
        """Check if smart-importer is installed and ready."""
        return self._available

    def train(self) -> bool:
        """
        Train the model from the ledger.

        Returns True if training succeeded.
        """
        if not self._available:
            return False

        try:
            from beancount import loader
            from smart_importer import PredictPostings

            entries, errors, options = loader.load_file(str(self.ledger_path))
            self._model = PredictPostings()
            self._model.train(entries)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    def predict(self, txn: Transaction) -> Transaction:
        """
        Predict the contra account for a transaction.

        Returns the transaction with prediction applied if successful.
        """
        if not self._available or self._model is None:
            return txn

        if txn.contra_account:  # Already has account
            return txn

        try:
            # Build a temporary beancount entry for prediction
            account, confidence = self._predict_account(txn)

            if confidence >= self.min_confidence:
                txn.contra_account = account
                txn.confidence = confidence
                txn.match_source = MatchSource.PREDICT
        except Exception:
            pass

        return txn

    def _predict_account(self, _txn: Transaction) -> tuple[str, float]:
        """
        Internal prediction logic.

        Returns (account, confidence).
        """
        # This is a simplified stub. The actual implementation would:
        # 1. Convert Transaction to a beancount TxnPosting
        # 2. Call smart_importer's predict method
        # 3. Extract the predicted account and confidence

        # For now, return empty prediction
        return ("", 0.0)


def apply_predictions(
    transactions: list[Transaction],
    ledger_path: Path,
    min_confidence: float = 0.8,
) -> list[Transaction]:
    """
    Apply ML predictions to transactions without contra accounts.

    Convenience function for common usage.
    """
    predictor = SmartPredictor(ledger_path, min_confidence)

    if not predictor.is_available:
        return transactions

    if not predictor.train():
        return transactions

    return [predictor.predict(txn) for txn in transactions]
