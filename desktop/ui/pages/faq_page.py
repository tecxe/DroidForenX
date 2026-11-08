"""FAQs page - the honest answers, where the chat assistant used to live.

There is no chat assistant in this build. In its place: general questions
about how the system works, answered the way the tool behaves. Answers are
structured - lists, steps and callouts - not paragraphs. Every answer is a
claim the test suite or a loader status can back.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                             QScrollArea, QPushButton)
from PyQt6.QtCore import Qt

#: Structured FAQ content. Each entry is (question, blocks) where a block is
#: ("p", text) | ("list", [items]) | ("steps", [items]) | ("note", text).
#: Categories group the questions so the page reads as a manual, not a list.
FAQ_CATEGORIES = [
    ("Getting started", [
        ("What is DroidForenX?", [
            ("p", "A standalone desktop toolkit for Android device forensics. "
                  "It identifies a handset, acquires its data over ADB into a "
                  "hashed evidence store, examines the preserved copy, and "
                  "produces court-ready reports - four stages, one workflow."),
            ("list", [
                "Single self-contained executable - no Python, no SDK needed",
                "ADB bundled inside",
                "Runs fully offline",
            ]),
        ]),
        ("How does Android forensics work?", [
            ("p", "The discipline of extracting, preserving and analysing "
                  "data from an Android handset without altering it."),
            ("steps", [
                "Identify the device and record it against the case",
                "Isolate it from the network so remote commands or incoming "
                "messages cannot change the store",
                "Acquire the data over ADB into a hashed evidence copy",
                "Examine that copy - never the original",
                "Report every finding, tied back to the acquisition that "
                "produced it",
            ]),
            ("note", "DroidForenX implements exactly that chain: four stages, "
                     "hashing on write, an append-only custody log, and "
                     "honest statuses (NOT_ACCESSIBLE, NOT_PRESENT) wherever "
                     "the platform refuses read access."),
        ]),
        ("How do I turn on Developer Mode and USB debugging?", [
            ("steps", [
                "Settings, then About phone",
                "Tap Build number seven times until a toast says Developer "
                "mode has been enabled",
                "Back to Settings, open System (or Advanced), then "
                "Developer options",
                "Switch on USB debugging and confirm the prompt",
                "Plug in a data-capable USB cable and accept the RSA "
                "fingerprint prompt on the handset",
            ]),
            ("note", "If the device shows as unauthorised, revoke the "
                     "authorisations on the phone and plug in again so the "
                     "prompt reappears."),
        ]),
        ("Do I need root?", [
            ("p", "No, not for logical acquisition."),
            ("list", [
                "Works unrooted: SMS, calls, contacts, media, documents, "
                "browser data, shared-storage social tiles",
                "Needs root: app-private data (reported NOT_ACCESSIBLE) and "
                "physical imaging",
            ]),
            ("note", "When root is refused, the phase says so instead of "
                     "quietly doing nothing."),
        ]),
    ]),
    ("How it works", [
        ("What are the four stages?", [
            ("steps", [
                "Identification - make, model, serial, Android version, build "
                "fingerprint, storage and installed packages, recorded "
                "against the case before anything is acquired",
                "Acquisition - the extraction itself, which also creates "
                "the forensic session",
                "Examination - analysis over the preserved copy, read-only",
                "Reporting - HTML, PDF, JSON and an XLSX verification "
                "workbook",
            ]),
            ("note", "Each stage hands the next a documented artifact; "
                     "nothing is analysed that was not first acquired and "
                     "hashed."),
        ]),
        ("How does acquisition work?", [
            ("p", "Pick an extraction level and the loaders to run. Level 1 "
                  "is the logical profile; higher levels add app data, "
                  "deleted data and, on a rooted handset, a physical image."),
            ("list", [
                "Every ADB call runs off the GUI thread - the window keeps "
                "drawing while the handset answers",
                "Progress in real percentages with a rate and an ETA",
                "Acquisition owns the channel while it runs",
            ]),
        ]),
        ("How is evidence kept trustworthy?", [
            ("list", [
                "SHA-256 hashes written on acquisition",
                "A manifest and an append-only custody log",
                "Custody survives re-opens; case metadata is not overwritten",
                "The XLSX verification workbook independently re-hashes "
                "every artifact",
            ]),
            ("note", "Tampering or a missing file shows up as a verification "
                     "failure, not a shrug. Reports are generated from the "
                     "same records, in plain English for lawyers and "
                     "judges."),
        ]),
        ("What reports can I export?", [
            ("list", [
                "HTML - the evidence report",
                "PDF - the narrative report: case header, device scope, "
                "per-loader outcomes, methodology",
                "JSON - machine-readable findings",
                "XLSX - the verification workbook that re-hashes everything",
            ]),
        ]),
    ]),
    ("Limits and honest answers", [
        ("What does NOT_ACCESSIBLE mean?", [
            ("p", "The loader ran and the platform refused it - almost "
                  "always because the data is app-private and needs root."),
            ("list", [
                "NOT_ACCESSIBLE - blocked by permissions (usually root)",
                "NOT_PRESENT - loader ran fine; the device held nothing of "
                "that type",
                "FAILED - the loader raised an error",
            ]),
            ("note", "It is a statement about permissions, not a claim that "
                     "the data does not exist. The report carries the same "
                     "status, so a court reader sees the same boundary."),
        ]),
        ("Can it recover deleted WhatsApp messages?", [
            ("list", [
                "Chat exports with full text, sender, side and timestamps",
                "A plain msgstore.db - messages, chats, contacts, media",
                "Crypt15 inventory for the encrypted store",
                "Notification previews captured on the device",
                ".trashed-* deleted media, original name and deletion time "
                "rebuilt from the filename",
            ]),
            ("note", "It does not read unallocated space - that needs a "
                     "physical image - and the tile says so when nothing was "
                     "found. Telegram exports, chats, groups, channels and "
                     "media are read the same honest way."),
        ]),
        ("Can I take a physical image without root?", [
            ("note", "No. On an unrooted handset the shell user is denied "
                     "every block device (65 of 65 on the TECNO used for "
                     "verification), so Create Image probes each partition "
                     "and reports 0 of 65 readable, marked needs root."),
            ("p", "On a rooted handset the acquisition phase images boot and "
                  "recovery over dd, hashes them, and files them."),
            ("note", "Even then, a physical read of an FBE/FDE handset "
                     "yields ciphertext for userdata. What remains readable "
                     "is the partition layout, boot/vendor/metadata "
                     "partitions, timestamps and unencrypted volumes."),
        ]),
        ("What does SIM Info actually read?", [
            ("p", "The UICC as the handset will expose it: every slot and "
                  "its state, carrier, network code, ICCID, IMSI and MSISDN "
                  "per subscription."),
            ("steps", [
                "dumpsys isub - first source for the subscriber number",
                "The iphonesubinfo parcel - second source",
                "content://telephony/siminfo - third source",
            ]),
            ("note", "Android redacts identifier tails; a redacted tail "
                     "means the platform refused the value, not that the "
                     "card is empty. An ICCID pasted into the number column "
                     "is rejected rather than reported as a phone number."),
        ]),
    ]),
    ("The tool itself", [
        ("What was the AI assistant meant to be?", [
            ("p", "Earlier builds carried a chat assistant in a side drawer - "
                  "a place to ask questions about the case while working."),
            ("note", "It was removed, on purpose. A forensic tool should not "
                     "chat, guess, or improvise answers about evidence; "
                     "every claim it makes has to be backed by a loader "
                     "status or a test. This FAQ page is its replacement: "
                     "the same questions, answered deterministically, in "
                     "plain language, with the limits stated instead of "
                     "hidden. Nothing here calls a network service."),
        ]),
        ("What is the God's eye button?", [
            ("p", "A scrcpy mirror of the connected device, launched from "
                  "the acquisition action row beside Create Image. scrcpy "
                  "turns the handset into a live, controllable screen - the "
                  "operator watches the evidence behaving and can drive it "
                  "with touch and keyboard, then detaches by closing the "
                  "window or pressing Stop."),
            ("steps", [
                "scrcpy ships inside the build - it works out of the box",
                "Press God's eye in the acquisition action row",
                "Drive the mirror; close it to detach",
            ]),
            ("note", "If a host install is missing entirely, the button "
                     "logs the install hint and stays harmless."),
        ]),
        ("How are cases managed?", [
            ("list", [
                "Correct a mistyped case number, title, investigator or "
                "status from Case Management",
                "Delete a case opened by mistake",
                "Deletion asks separately whether the evidence folder goes "
                "with it - folders are left alone unless you say otherwise",
            ]),
            ("note", "A path that resolves outside the evidence root is "
                     "refused rather than removed."),
        ]),
        ("Does it need internet, and how do I verify the download?", [
            ("p", "No - it runs fully offline. The only network call it "
                  "makes is the optional update check."),
            ("steps", [
                "Every release ships a .sha256 file beside the executable",
                "PowerShell: (Get-FileHash DroidForenX_Installer.exe "
                "-Algorithm SHA256).Hash",
                "Compare it with the hash on the download page",
            ]),
            ("note", "The portable build is reproducible: two builds of the "
                     "same commit with requirements-lock.txt installed "
                     "produce the same SHA-256."),
        ]),
    ]),
]


def _flat():
    for _category, items in FAQ_CATEGORIES:
        yield from items


def _flat_text(blocks):
    parts = []
    for kind, payload in blocks:
        if kind in ("p", "note"):
            parts.append(payload)
        else:
            parts.extend(payload)
    return " ".join(parts)


#: Flat (question, answer-text) view, for tests and plain-text consumers.
FAQS = [(q, _flat_text(blocks)) for q, blocks in _flat()]


class FaqPage(QWidget):
    """Categorised, structured FAQs inside the page's own scroller."""

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
        for category, items in FAQ_CATEGORIES:
            header = QLabel(category.upper())
            header.setStyleSheet(
                "font-size: 13px; font-weight: 700; color: #f59e0b; "
                "letter-spacing: 2px; background: transparent; border: none;")
            layout.addWidget(header)
            for question, blocks in items:
                layout.addWidget(self._card(question, blocks))

        note = QLabel(
            "Still stuck? The acquisition console log says what the handset "
            "actually answered, and the report carries the same statuses - "
            "start there before assuming the data is not there.")
        note.setWordWrap(True)
        note.setStyleSheet(
            "font-size: 13px; color: #64748b; background: transparent; "
            "border: none;")
        layout.addWidget(note)

    def _card(self, question, blocks):
        card = QFrame()
        card.setObjectName("faqCard")
        card.setStyleSheet(
            "#faqCard { background: #111827; border: 1px solid #1e293b; "
            "border-radius: 10px; }")
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

        body = QWidget(card)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 0, 20, 18)
        body_layout.setSpacing(12)
        for kind, payload in blocks:
            body_layout.addWidget(self._block(kind, payload))
        body.setVisible(False)
        card_layout.addWidget(body)

        def _toggle(checked, ask=ask, body=body):
            body.setVisible(checked)

        ask.toggled.connect(_toggle)
        self.faq_items.append((ask, body))
        return card

    @staticmethod
    def _block(kind, payload):
        if kind == "p":
            label = QLabel(payload)
            label.setStyleSheet(
                "font-size: 15px; color: #94a3b8; background: transparent; "
                "border: none;")
            label.setWordWrap(True)
            return label
        if kind == "note":
            label = QLabel("Note: " + payload)
            label.setWordWrap(True)
            label.setStyleSheet(
                "font-size: 14px; color: #f59e0b; background: rgba(245,158,11,.08); "
                "border: 1px solid rgba(245,158,11,.35); border-radius: 8px; "
                "padding: 10px 14px;")
            return label
        rows = QVBoxLayout()
        rows.setSpacing(8)
        rows.setContentsMargins(4, 0, 0, 0)
        wrap = QWidget()
        wrap.setLayout(rows)
        for i, item in enumerate(payload, 1):
            row = QHBoxLayout()
            row.setSpacing(12)
            marker = QLabel(f"{i}." if kind == "steps" else "•")
            marker.setStyleSheet(
                "font-size: 15px; font-weight: 700; color: #f59e0b; "
                "background: transparent; border: none;")
            row.addWidget(marker)
            text = QLabel(item)
            text.setWordWrap(True)
            text.setStyleSheet(
                "font-size: 15px; color: #94a3b8; background: transparent; "
                "border: none;")
            row.addWidget(text, 1)
            rows.addLayout(row)
        return wrap
