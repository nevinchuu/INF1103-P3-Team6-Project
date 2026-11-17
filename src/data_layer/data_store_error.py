class DataStoreError(Exception):
    """The saved jobs can't be reached (database down, wrong address...). Raised by DataManager, caught by IOManager."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
