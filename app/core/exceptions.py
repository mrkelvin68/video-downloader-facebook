class CsvValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


class VideoImportValidationError(ValueError):
    """Validation errors that can be shown together in the import editor."""

    def __init__(
        self,
        errors: list[str],
        *,
        db_duplicates: list[dict[str, object]] | None = None,
    ) -> None:
        self.errors = errors
        self.db_duplicates = list(db_duplicates or [])
        super().__init__("\n\n".join(errors))


class ImportConfirmationRequired(ValueError):
    pass


class ImportBlockedError(ValueError):
    pass
