class CsvValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


class ImportConfirmationRequired(ValueError):
    pass


class ImportBlockedError(ValueError):
    pass
