"""FAQs page - the honest answers, where the chat assistant used to live.

There is no chat assistant in this build. In its place: a short list of
questions officers actually ask, answered the way the tool behaves - what it
reads without root, what it refuses, what it cannot do, and how to check its
work. Every answer here is a claim the test suite or a loader status can back.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QFrame, QScrollArea,
                             QPushButton)
from PyQt6.QtCore import Qt

#: (question, answer) pairs - general questions about how the system works.
#: Answers state limits instead of hiding them.
FAQS = [
    ("What is DroidForenX?",
     "A standalone desktop toolkit for Android device forensics. It identifies "
     "a handset, acquires its data over ADB into a hashed evidence store, "
     "examines the preserved copy, and produces court-ready reports - four "
     "stages, one workflow. It ships as a single self-contained executable "
     "with ADB bundled inside, so the target machine needs neither Python "
     "nor the Android SDK."),

    ("What was the AI assistant meant to be?",
     "Earlier builds carried a chat assistant in a side drawer - a place to "
     "ask questions about the case while working. It was removed, on purpose. "
     "A forensic tool should not chat, guess, or improvise answers about "
     "evidence; every claim it makes has to be backed by a loader status or "
     "a test. This FAQ page is its replacement: the same questions, answered "
     "deterministically, in plain language, with the limits stated instead "
     "of hidden. Nothing here calls a network service."),

    ("What are the four stages?",
     "1. Identification - make, model, serial, Android version, build "
     "fingerprint, storage and installed packages, recorded against the case "
     "before anything is acquired. 2. Acquisition - the extraction itself, "
     "which also creates the forensic session. 3. Examination - analysis "
     "over the preserved copy, read-only. 4. Reporting - HTML, PDF, JSON "
     "and an XLSX verification workbook. Each stage hands the next a "
     "documented artifact; nothing is analysed that was not first acquired "
     "and hashed."),

    ("How does acquisition work?",
     "Pick an extraction level and the loaders to run. Level 1 is the logical "
     "profile; higher levels add app data, deleted data and, on a rooted "
     "handset, a physical image. Every ADB call runs off the GUI thread, so "
     "the window keeps drawing while the handset answers, and progress is "
     "reported in real percentages with a rate and an ETA. The acquisition "
     "phase creates the forensic session the report later cites."),

    ("How is evidence kept trustworthy?",
     "Artifacts are hashed (SHA-256) on write, recorded in a manifest, and "
     "entered into an append-only custody log. Custody survives re-opens and "
     "case metadata is not overwritten. The verification workbook "
     "independently re-hashes every artifact, so tampering or a missing file "
     "shows up as a failure rather than a shrug. Reports are generated from "
     "the same records, in plain English for lawyers and judges."),

    ("How does Android forensics work?",
     "Android forensics is the discipline of extracting, preserving and "
     "analysing data from an Android handset without altering it. In "
     "practice: identify the device, isolate it from the network so remote "
     "commands or incoming messages cannot change the store, acquire the "
     "data over ADB into a hashed evidence copy, then examine that copy - "
     "never the original - with analysis modules for messages, calls, "
     "contacts, media, app data and deleted traces. The report ties every "
     "finding back to the acquisition that produced it. DroidForenX "
     "implements exactly that chain: four stages, hashing on write, an "
     "append-only custody log, and honest statuses (NOT_ACCESSIBLE, "
     "NOT_PRESENT) wherever the platform refuses read access."),

    ("How do I turn on Developer Mode and USB debugging?",
     "On the handset: open Settings, then About phone, then tap Build "
     "number seven times until a toast says Developer mode has been "
     "enabled. Go back to Settings, open System (or Advanced) and find "
     "Developer options, then switch on USB debugging and confirm the "
     "prompt. Plug the phone in with a data-capable USB cable, accept the "
     "RSA fingerprint prompt on the handset when it appears, and the device "
     "will show as authorised in DroidForenX. If it shows as unauthorised, "
     "revoke the authorisations on the phone and plug in again so the "
     "prompt reappears."),

    ("Do I need root?",
     "No, not for logical acquisition. SMS, calls, contacts, media, "
     "documents, browser data and the social tiles that read shared storage "
     "all work unrooted. Root matters for two things: app-private data "
     "(reported as NOT_ACCESSIBLE, never silently dropped) and physical "
     "imaging. When root is refused, the phase says so instead of quietly "
     "doing nothing."),

    ("What does NOT_ACCESSIBLE mean?",
     "The loader ran and the platform refused it - almost always because the "
     "data is app-private and needs root. It is a statement about the "
     "handset's permissions, not a claim that the data does not exist. The "
     "report carries the same status, so a court reader sees the boundary "
     "the same way the operator did. NOT_PRESENT is different again: the "
     "loader ran fine and the device held nothing of that type."),

    ("What is the God's eye button?",
     "A scrcpy mirror of the connected device, launched from the acquisition "
     "action row beside Create Image. scrcpy turns the handset into a live, "
     "controllable screen - the operator watches the evidence behaving and "
     "can drive it with touch and keyboard, then detaches by closing the "
     "window or pressing Stop. Install it with: "
     "winget install --id Genymobile.scrcpy (Windows) or "
     "sudo apt install scrcpy (Linux). If scrcpy is absent, the button logs "
     "the install hint and stays harmless."),

    ("Can it recover deleted WhatsApp messages?",
     "It recovers what survives on shared storage: chat exports, a plain "
     "msgstore.db, the crypt15 inventory for the encrypted store, "
     "notification previews, and .trashed-* deleted media with the original "
     "name and deletion time rebuilt from the filename. It does not read "
     "unallocated space - that needs a physical image - and the tile says "
     "so when nothing was found. Telegram exports, chats, groups, channels "
     "and media are read the same honest way."),

    ("Can I take a physical image without root?",
     "No. The shell user is denied every block device on an unrooted handset "
     "(65 of 65 on the TECNO used for verification), so Create Image probes "
     "each partition and reports 0 of 65 readable, marked needs root. On a "
     "rooted handset the acquisition phase images boot and recovery over "
     "dd, hashes them, and files them. Even then, a physical read of an "
     "FBE/FDE handset yields ciphertext for userdata - what remains readable "
     "is the partition layout, boot/vendor/metadata partitions, timestamps "
     "and unencrypted volumes."),

    ("How are cases managed?",
     "Every case the operator opens is listed in Case Management with its "
     "investigator, status and evidence folder. A mistyped case number, "
     "title, investigator or status can be corrected, and a case opened by "
     "mistake can be deleted - with a separate question about whether the "
     "evidence folder goes with it. Folders are left alone unless you say "
     "otherwise, and a path outside the evidence root is refused rather "
     "than removed."),

    ("What does SIM Info actually read?",
     "The UICC as the handset will expose it: every slot and its state, "
     "carrier, network code, ICCID, IMSI and MSISDN per subscription. The "
     "subscriber number is read from three places in order - dumpsys isub, "
     "the iphonesubinfo parcel, and content://telephony/siminfo - and an "
     "ICCID pasted into the number column is rejected rather than reported "
     "as a phone number. Android's redaction of identifier tails is stated "
     "in the record, and a page-level note says what the handset cannot "
     "give up."),

    ("Does it need internet, and how do I verify the download?",
     "No - it runs fully offline; the only network call it makes is the "
     "optional update check. To verify a download, every release ships a "
     ".sha256 file beside the executable: in PowerShell, "
     "(Get-FileHash DroidForenX_Installer.exe -Algorithm SHA256).Hash, then "
     "compare it with the hash on the download page. The portable build is "
     "reproducible - two builds of the same commit with "
     "requirements-lock.txt installed produce the same SHA-256."),
]


class FaqPage(QWidget):
    """Expandable answers in a tool box, inside the page's own scroller."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(24)
        scroll.setWidget(container)

        title = QLabel("Frequently Asked Questions")
        title.setStyleSheet(
            "font-size: 26px; font-weight: 700; color: #e2e8f0; "
            "background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel(
            "How the system works, in plain language - the workflow, the "
            "evidence rules, and what the tool will and will not do. This "
            "page is what the chat assistant was replaced with: the same "
            "questions, answered deterministically.")
        subtitle.setStyleSheet(
            "font-size: 15px; color: #64748b; background: transparent; "
            "border: none;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.faq_items = []
        for question, answer in FAQS:
            card = QFrame(container)
            card.setStyleSheet(
                "#faqCard { background: #111827; border: 1px solid #1e293b; "
                "border-radius: 10px; }")
            card.setObjectName("faqCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(0)

            ask = QPushButton(question, card)
            ask.setCheckable(True)
            ask.setCursor(Qt.CursorShape.PointingHandCursor)
            ask.setStyleSheet(
                "QPushButton { background: transparent; color: #e2e8f0; "
                "border: none; border-radius: 10px; padding: 18px 20px; "
                "font-size: 17px; font-weight: 600; text-align: left; }"
                "QPushButton:checked { color: #22d3ee; }")
            card_layout.addWidget(ask)

            body = QLabel(answer, card)
            body.setWordWrap(True)
            body.setStyleSheet(
                "font-size: 15px; color: #94a3b8; background: transparent; "
                "border: none; padding: 0 20px 18px 20px;")
            body.setVisible(False)
            card_layout.addWidget(body)

            def _toggle(checked, ask=ask, body=body):
                body.setVisible(checked)

            ask.toggled.connect(_toggle)
            self.faq_items.append((ask, body))
            layout.addWidget(card)
        # first answer open by default so the page never looks empty
        if self.faq_items:
            self.faq_items[0][0].setChecked(True)

        note = QLabel(
            "Still stuck? The acquisition console log says what the handset "
            "actually answered, and the report carries the same statuses - "
            "start there before assuming the data is not there.")
        note.setWordWrap(True)
        note.setStyleSheet(
            "font-size: 12px; color: #64748b; background: transparent; "
            "border: none;")
        layout.addWidget(note)
