from __future__ import annotations

from urllib.parse import urlparse

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "relio"
COLLECTION_NAME = "company_briefs"


def _get_collection() -> Collection:
    client = MongoClient(MONGO_URI)
    return client[DB_NAME][COLLECTION_NAME]


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def upsert_brief(brief_dict: dict) -> str:
    """
    Upsert a CompanyBrief dict into MongoDB, keyed by domain.
    Returns the upserted/matched document ID as a string.
    """
    collection = _get_collection()
    domain = _domain_from_url(brief_dict["metadata"]["url"])

    result = collection.update_one(
        {"domain": domain},
        {"$set": {**brief_dict, "domain": domain}},
        upsert=True,
    )

    doc_id = result.upserted_id or collection.find_one({"domain": domain}, {"_id": 1})["_id"]
    return str(doc_id)
