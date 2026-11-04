from PySide6.QtCore import QSettings, QThreadPool
from app.ui.pages.viral_reels_page import CancellableWorker
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.apify_facebook_provider import ApifyFacebookReelsProvider
from app.services.meta_graph_provider import MetaGraphReelsProvider
from app.services.credential_service import delete_apify_token, delete_meta_token, get_apify_token, get_meta_token, set_apify_token, set_meta_token

SETTINGS_ORGANIZATION = "VideoDownloader"
SETTINGS_APPLICATION = "VideoDownloaderFacebook"


def viral_settings() -> QSettings:
    return QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)


class ViralProviderDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Viral Reels Provider")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Configure authorized provider credentials. Tokens stay in Windows Credential Manager."))
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems(["Apify Facebook Reels Scraper", "Meta Graph API"])
        self.provider.setCurrentText(viral_settings().value("viral_reels/provider", "Apify Facebook Reels Scraper"))
        self.token = QLineEdit(get_meta_token() if self.provider.currentText() == "Meta Graph API" else get_apify_token())
        self.provider.currentTextChanged.connect(self._provider_changed)
        self.provider_name = self.provider
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Provider", self.provider)
        form.addRow("API token", self.token)
        layout.addLayout(form)
        self.status = QLabel()
        layout.addWidget(self.status)
        test = QPushButton("Test Connection")
        test.clicked.connect(self.test_connection)
        layout.addWidget(test)
        delete = QPushButton("Delete saved token")
        delete.clicked.connect(self.delete_token)
        layout.addWidget(delete)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _provider_changed(self, name: str) -> None:
        self.token.setText(get_meta_token() if name == "Meta Graph API" else get_apify_token())
        self.provider.setToolTip(name)

    def _provider(self, token: str):
        return MetaGraphReelsProvider(token) if self.provider.currentText() == "Meta Graph API" else ApifyFacebookReelsProvider(token)

    def test_connection(self):
        token = self.token.text().strip()
        provider_name = self.provider.currentText()
        if not token:
            self.status.setText(f"{provider_name} token is required.")
            return
        self.status.setText(f"Testing {provider_name}…")
        worker = CancellableWorker(lambda _cancel, _progress: self._provider(token).test_connection())
        worker.signals.result.connect(lambda _result: self.status.setText("Connection successful."))
        worker.signals.error.connect(self.status.setText)
        QThreadPool.globalInstance().start(worker)

    def delete_token(self):
        try:
            (delete_meta_token if self.provider.currentText() == "Meta Graph API" else delete_apify_token)()
        except Exception as error:
            self.status.setText(f"Credential storage failed: {error}")
            return
        self.token.clear()
        self.status.setText("Saved token deleted.")

    def accept(self):
        provider_name = self.provider.currentText()
        token = self.token.text().strip()
        try:
            if token:
                (set_meta_token if provider_name == "Meta Graph API" else set_apify_token)(token)
            settings = viral_settings()
            settings.setValue("viral_reels/provider", provider_name)
            settings.sync()
            if settings.status() != QSettings.Status.NoError or settings.value("viral_reels/provider") != provider_name:
                raise RuntimeError("Provider selection could not be saved")
        except ValueError as error:
            self.status.setText(str(error))
            return
        except Exception as error:
            self.status.setText(f"Provider save failed; previous configuration kept: {error}")
            return
        super().accept()
