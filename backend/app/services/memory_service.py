"""Persistent research memory helpers."""

from __future__ import annotations

import math
import re
import uuid
import hashlib
from collections import Counter

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ChatSession, SessionEmbedding, UserResearchProfile

_OBJECT_RE = re.compile(
    r"\b(M\s?\d+|NGC\s?\d+|IC\s?\d+|Pleiades|Hyades|Orion Nebula|Crab Nebula|M31|M42|M45)\b",
    re.IGNORECASE,
)
_DB_TOKENS = {
    "gaia", "sdss", "simbad", "vizier", "mast", "jwst", "ned", "desi",
    "lamost", "chandra", "alma", "irsa", "2mass", "allwise",
}
_METHOD_TOKENS = {
    "adql", "isochrone", "lomb-scargle", "periodogram", "bpt", "hr", "gaussian",
    "spectral", "pipeline", "crossmatch", "lightcurve", "photo-z", "sed",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_.+-]{2,}", text.lower())


def _hash_embed(text: str, dim: int = 256) -> list[float]:
    vec = np.zeros(dim, dtype=float)
    for token in _tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:8], "big") % dim
        vec[idx] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm:
        vec /= norm
    return vec.tolist()


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    va = np.array(a, dtype=float)
    vb = np.array(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def _extract_session_features(messages: list[dict]) -> dict:
    user_texts: list[str] = []
    assistant_texts: list[str] = []
    all_text_parts: list[str] = []
    databases: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    objects: Counter[str] = Counter()

    for message in messages or []:
        content = str(message.get("content", "") or "")
        all_text_parts.append(content)
        role = message.get("role")
        if role == "user":
            user_texts.append(content)
        elif role == "assistant":
            assistant_texts.append(content)

        lower = content.lower()
        for db_name in _DB_TOKENS:
            if db_name in lower:
                databases[db_name] += 1
        for method in _METHOD_TOKENS:
            if method in lower:
                methods[method] += 1
        for match in _OBJECT_RE.findall(content):
            objects[match.upper().replace(" ", "")] += 1

        for action in message.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("action", "")).lower()
            if action_name in {"search", "search_objects"}:
                for source in action.get("sources") or []:
                    databases[str(source).lower()] += 1
                query = str(action.get("query", ""))
                for match in _OBJECT_RE.findall(query):
                    objects[match.upper().replace(" ", "")] += 1
            elif action_name in {"run_adql", "adql"}:
                databases[str(action.get("service", "gaia")).lower()] += 1
                methods["adql"] += 1
            elif action_name == "run_python":
                methods["python"] += 1
            elif "pipeline" in action_name:
                methods["pipeline"] += 1

    full_text = "\n".join(part for part in all_text_parts if part)
    summary_parts = []
    if user_texts:
        summary_parts.append(f"Research goals: {' | '.join(user_texts[:3])}")
    if databases:
        summary_parts.append(f"Data sources: {', '.join(name for name, _ in databases.most_common(5))}")
    if methods:
        summary_parts.append(f"Methods: {', '.join(name for name, _ in methods.most_common(5))}")
    if assistant_texts:
        summary_parts.append(f"Findings: {' '.join(assistant_texts[-2:])[:500]}")

    return {
        "summary": " ".join(summary_parts)[:1200] or full_text[:1200] or "Interactive astronomy research session.",
        "objects": [name for name, _ in objects.most_common(10)],
        "databases": [name for name, _ in databases.most_common(10)],
        "methods": [name for name, _ in methods.most_common(10)],
        "keywords": [token for token, _ in Counter(_tokenize(full_text)).most_common(20) if len(token) > 3],
        "message_count": len(messages or []),
        "full_text": full_text,
    }


def _assess_expertise(features: dict) -> str:
    score = 0
    score += 2 if "adql" in features["methods"] else 0
    score += 2 if "pipeline" in features["methods"] else 0
    score += 1 if len(features["databases"]) >= 3 else 0
    score += 1 if features["message_count"] >= 8 else 0
    if score >= 5:
        return "advanced"
    if score >= 2:
        return "intermediate"
    return "beginner"


class MemoryService:
    async def ensure_profile(self, user_id: uuid.UUID, db: AsyncSession) -> UserResearchProfile:
        profile = (
            await db.execute(select(UserResearchProfile).where(UserResearchProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            profile = UserResearchProfile(
                user_id=user_id,
                frequently_queried_objects=[],
                preferred_databases=[],
                preferred_analysis_methods=[],
                research_interests=[],
                past_hypotheses=[],
                preferred_plotting_style={"colormap": "viridis", "figure_size": [10, 8], "font_size": 12},
                memory_enabled=False,
            )
            db.add(profile)
            await db.flush()
        return profile

    async def refresh_session_memory(self, user_id: uuid.UUID, session_id: uuid.UUID, db: AsyncSession) -> None:
        profile = await self.ensure_profile(user_id, db)
        if not profile.memory_enabled:
            return

        session = (
            await db.execute(
                select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
            )
        ).scalar_one_or_none()
        if session is None:
            return

        features = _extract_session_features(session.messages or [])
        object_counter = Counter({
            item.get("name", ""): int(item.get("count", 0))
            for item in (profile.frequently_queried_objects or [])
            if isinstance(item, dict) and item.get("name")
        })
        for obj in features["objects"]:
            object_counter[obj] += 1

        profile.frequently_queried_objects = [
            {"name": name, "count": count}
            for name, count in object_counter.most_common(15)
        ]
        profile.preferred_databases = features["databases"][:8]
        profile.preferred_analysis_methods = features["methods"][:8]
        profile.research_interests = features["keywords"][:12]
        profile.expertise_level = _assess_expertise(features)

        existing_embedding = (
            await db.execute(
                select(SessionEmbedding).where(
                    SessionEmbedding.user_id == user_id,
                    SessionEmbedding.session_id == session_id,
                )
            )
        ).scalar_one_or_none()

        if existing_embedding is None:
            existing_embedding = SessionEmbedding(
                user_id=user_id,
                session_id=session_id,
                summary=features["summary"],
                embedding=_hash_embed(features["summary"]),
                key_objects=features["objects"],
                key_methods=features["methods"],
                key_findings=features["keywords"][:5],
            )
            db.add(existing_embedding)
        else:
            existing_embedding.summary = features["summary"]
            existing_embedding.embedding = _hash_embed(features["summary"])
            existing_embedding.key_objects = features["objects"]
            existing_embedding.key_methods = features["methods"]
            existing_embedding.key_findings = features["keywords"][:5]

        await db.flush()

    async def get_relevant_history(
        self, user_id: uuid.UUID, current_query: str, db: AsyncSession, top_k: int = 3
    ) -> list[dict]:
        query_vec = _hash_embed(current_query)
        rows = (
            await db.execute(
                select(SessionEmbedding).where(SessionEmbedding.user_id == user_id)
            )
        ).scalars().all()
        ranked = sorted(
            [
                {
                    "session_id": str(row.session_id),
                    "summary": row.summary,
                    "date": row.created_at.isoformat() if row.created_at else None,
                    "objects": row.key_objects or [],
                    "similarity": _cosine(query_vec, row.embedding),
                }
                for row in rows
            ],
            key=lambda item: item["similarity"],
            reverse=True,
        )
        return ranked[:top_k]

    async def get_user_context(self, user_id: uuid.UUID, current_query: str, db: AsyncSession) -> str:
        profile = (
            await db.execute(select(UserResearchProfile).where(UserResearchProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None or not profile.memory_enabled:
            return ""

        history = await self.get_relevant_history(user_id, current_query, db, top_k=3)
        frequently_queried = ", ".join(
            f"{item['name']} ({item['count']}x)"
            for item in (profile.frequently_queried_objects or [])[:5]
            if isinstance(item, dict) and item.get("name")
        )
        recent_lines = [
            f"- {item['date'] or 'recent'}: {item['summary'][:180]}"
            for item in history
        ]
        return (
            f"User profile: {profile.expertise_level} researcher. "
            f"Interests: {', '.join(profile.research_interests or []) or 'not enough history yet'}. "
            f"Frequently queried objects: {frequently_queried or 'none yet'}. "
            f"Preferred databases: {', '.join(profile.preferred_databases or []) or 'mixed'}. "
            f"Preferred methods: {', '.join(profile.preferred_analysis_methods or []) or 'mixed'}.\n"
            f"Recent related sessions:\n" + ("\n".join(recent_lines) if recent_lines else "- none")
        )

    async def list_history(self, user_id: uuid.UUID, db: AsyncSession, query: str | None = None) -> list[dict]:
        rows = (
            await db.execute(
                select(SessionEmbedding)
                .where(SessionEmbedding.user_id == user_id)
                .order_by(SessionEmbedding.created_at.desc())
            )
        ).scalars().all()
        items = [
            {
                "id": str(row.id),
                "session_id": str(row.session_id),
                "summary": row.summary,
                "objects": row.key_objects or [],
                "methods": row.key_methods or [],
                "findings": row.key_findings or [],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        if query:
            q = query.lower()
            items = [item for item in items if q in item["summary"].lower()]
        return items

    async def delete_all_memory(self, user_id: uuid.UUID, db: AsyncSession) -> None:
        await db.execute(delete(SessionEmbedding).where(SessionEmbedding.user_id == user_id))
        profile = (
            await db.execute(select(UserResearchProfile).where(UserResearchProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile:
            profile.frequently_queried_objects = []
            profile.preferred_databases = []
            profile.preferred_analysis_methods = []
            profile.research_interests = []
            profile.past_hypotheses = []
            profile.expertise_level = "beginner"
            profile.memory_enabled = False


memory_service = MemoryService()
