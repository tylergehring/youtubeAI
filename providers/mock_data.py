"""Canned fake content shared by the mock providers (Milestone 1 only).

The content is a short, plausible TCP lesson so the fake pipeline produces schema-valid,
human-readable artifacts. Nothing here calls an external service.
"""

from __future__ import annotations

from typing import Any

HOOK = (
    "Every web page you load depends on a promise: the bytes you send will arrive, in order, "
    "exactly once. TCP keeps that promise, even though the network underneath keeps losing "
    "packets."
)
SYN = (
    "Before any data moves, the two machines have to agree to talk. The client starts by "
    "sending a SYN packet to the server, carrying a randomly chosen starting sequence number."
)
SYN_ACK = (
    "The server answers with a SYN-ACK. It acknowledges the client's number and offers its "
    "own. The client finishes with an ACK, and now both sides know the other is listening."
)
RELIABILITY = (
    "From here, every byte gets a sequence number. The receiver acknowledges what it has "
    "seen, and the sender retransmits anything that goes unacknowledged. Lost, duplicated or "
    "reordered packets are repaired before the application ever notices."
)
CONCLUSION = (
    "So TCP builds a reliable, ordered stream on top of an unreliable network, using "
    "sequence numbers, acknowledgements and retransmission. Next time a page loads, remember "
    "the handshake that happened first."
)


def knowledge(topic: str) -> dict[str, Any]:
    return {
        "topic": topic,
        "concepts": [
            {
                "name": "Three-way handshake",
                "description": "SYN, SYN-ACK, ACK exchange that opens a connection.",
                "importance": 0.95,
            },
            {
                "name": "Sequence numbers",
                "description": "Numbering of bytes so the receiver can order and de-duplicate.",
                "importance": 0.9,
            },
            {
                "name": "Retransmission",
                "description": "Resending data that was not acknowledged in time.",
                "importance": 0.85,
            },
        ],
        "entities": [
            {"name": "Client", "kind": "host"},
            {"name": "Server", "kind": "host"},
        ],
        "relationships": [
            {"subject": "Client", "predicate": "sends SYN to", "object": "Server"},
            {"subject": "Server", "predicate": "replies SYN-ACK to", "object": "Client"},
        ],
        "claims": [
            {
                "id": "c1",
                "text": "TCP provides reliable, ordered delivery of a byte stream.",
                "status": "SOURCE_CLAIM",
                "confidence": 0.9,
            },
            {
                "id": "c2",
                "text": "A TCP connection is opened with a three-way handshake.",
                "status": "SOURCE_CLAIM",
                "confidence": 0.9,
            },
            {
                "id": "c3",
                "text": "TCP uses sequence numbers and acknowledgements to detect loss.",
                "status": "SOURCE_CLAIM",
                "confidence": 0.85,
            },
            {
                "id": "c4",
                "text": "Unacknowledged data is retransmitted by the sender.",
                "status": "INFERENCE",
                "confidence": 0.8,
            },
        ],
        "definitions": [
            {
                "term": "SYN",
                "definition": "A segment flag requesting to synchronise sequence numbers.",
            }
        ],
        "examples": [
            {"title": "Loading a web page", "description": "Browser and server open a connection."}
        ],
        "chronology": [],
        "uncertainties": [],
        "source_refs": [{"label": "RFC 9293", "url": "https://www.rfc-editor.org/rfc/rfc9293"}],
    }


def lesson(topic: str, audience: str) -> dict[str, Any]:
    return {
        "title": topic,
        "hook": HOOK,
        "audience": audience,
        "prerequisites": ["What an IP address is"],
        "objectives": [
            "Explain why TCP needs a handshake",
            "Describe how sequence numbers enable reliability",
        ],
        "sections": [
            {"id": "intro", "title": "Why reliability is hard", "concepts": []},
            {
                "id": "handshake",
                "title": "Opening a connection",
                "concepts": ["Three-way handshake"],
                "examples": ["Loading a web page"],
            },
            {
                "id": "reliability",
                "title": "Keeping data intact",
                "concepts": ["Sequence numbers", "Retransmission"],
            },
            {"id": "wrap_up", "title": "Putting it together", "concepts": []},
        ],
        "conclusion": CONCLUSION,
        "estimated_duration_s": 60,
    }


def script() -> dict[str, Any]:
    return {
        "sections": [
            {"id": "intro", "purpose": "hook", "narration": HOOK, "claim_ids": ["c1"]},
            {
                "id": "handshake_syn",
                "purpose": "explanation",
                "narration": SYN,
                "claim_ids": ["c2"],
            },
            {
                "id": "handshake_ack",
                "purpose": "example",
                "narration": SYN_ACK,
                "claim_ids": ["c2"],
            },
            {
                "id": "reliability",
                "purpose": "explanation",
                "narration": RELIABILITY,
                "claim_ids": ["c3", "c4"],
            },
            {"id": "conclusion", "purpose": "conclusion", "narration": CONCLUSION},
        ]
    }


def _computer_server() -> list[dict[str, Any]]:
    return [
        {"type": "computer", "id": "client", "label": "Client", "position": [0.2, 0.5]},
        {"type": "server", "id": "server", "label": "Server", "position": [0.8, 0.5]},
    ]


def scenes() -> dict[str, Any]:
    return {
        "scenes": [
            {
                "scene_id": 1,
                "section_id": "intro",
                "narration": HOOK,
                "estimated_duration_s": 8,
                "visual_type": "title",
                "objects": [
                    {"type": "text", "id": "title", "text": "How TCP Works", "position": [0.5, 0.5]}
                ],
                "animations": [{"type": "fade_in", "target": "title", "start": 0.0, "end": 0.3}],
                "text": ["How TCP Works"],
            },
            {
                "scene_id": 2,
                "section_id": "handshake_syn",
                "narration": SYN,
                "estimated_duration_s": 8,
                "visual_type": "network_diagram",
                "objects": _computer_server(),
                "animations": [
                    {
                        "type": "packet",
                        "from": "client",
                        "to": "server",
                        "label": "SYN",
                        "start": 0.4,
                        "end": 0.9,
                    }
                ],
                "emphasis": ["SYN"],
            },
            {
                "scene_id": 3,
                "section_id": "handshake_ack",
                "narration": SYN_ACK,
                "estimated_duration_s": 8,
                "visual_type": "network_diagram",
                "objects": _computer_server(),
                "animations": [
                    {
                        "type": "packet",
                        "from": "server",
                        "to": "client",
                        "label": "SYN-ACK",
                        "start": 0.1,
                        "end": 0.5,
                    },
                    {
                        "type": "packet",
                        "from": "client",
                        "to": "server",
                        "label": "ACK",
                        "start": 0.6,
                        "end": 0.95,
                    },
                ],
                "emphasis": ["SYN-ACK", "ACK"],
            },
            {
                "scene_id": 4,
                "section_id": "reliability",
                "narration": RELIABILITY,
                "estimated_duration_s": 10,
                "visual_type": "network_diagram",
                "objects": _computer_server(),
                "animations": [
                    {
                        "type": "packet",
                        "from": "client",
                        "to": "server",
                        "label": "seq 1",
                        "start": 0.1,
                        "end": 0.4,
                    },
                    {"type": "pulse", "target": "server", "start": 0.5, "end": 0.7},
                ],
            },
            {
                "scene_id": 5,
                "section_id": "conclusion",
                "narration": CONCLUSION,
                "estimated_duration_s": 8,
                "visual_type": "text_slide",
                "objects": [
                    {
                        "type": "text",
                        "id": "recap",
                        "text": "Reliable on top of unreliable",
                        "position": [0.5, 0.5],
                    }
                ],
                "animations": [{"type": "type", "target": "recap", "start": 0.0, "end": 0.5}],
                "text": ["Reliable on top of unreliable"],
                "transition": "fade",
            },
        ]
    }
