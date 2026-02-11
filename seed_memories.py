#!/usr/bin/env python3
"""Seed Conduit's memory database with bootstrap knowledge about Joseph."""

import asyncio
import sys
sys.path.insert(0, ".")

from server import db

MEMORIES = [
    # === FACTS (importance 8-10) ===
    ("fact", "Joseph Loftus — food service operations manager at Culinart (Compass Group)", 10),
    ("fact", "Primary site: Lockheed Martin Building 100 in Pennsylvania", 10),
    ("fact", "POS system generates Financial Detail Report PDFs for daily sales", 8),
    ("fact", "Inventory system: OrderMaestro/FoodIQ — strict 23-column xlsx upload", 8),
    ("fact", "Sales entry via corporate back office web portal (Daily Sales Entry)", 8),
    ("fact", "PA sales tax rate is ~6%, cash is <1% of transactions", 7),
    ("fact", "All cafe sales go into the Lunch GL line, not Breakfast", 8),
    ("fact", "Weekday average sales ~$5,750/day, Friday ~$850", 7),
    ("fact", "Food cost target: sub-45% for Compass Group managed accounts", 9),
    ("fact", "Food cost = (Beginning Inventory + Purchases - Ending Inventory) / Sales", 8),
    ("fact", "Credit cards all combine into AMX-coded CC Clearing line", 7),
    ("fact", "Register readings: back office ~1M range, POS GC ~3.2M range — different counters", 8),
    ("fact", "Open balance: do NOT subtract from Lunch — enter full POS net sales, take over/short", 9),
    ("fact", "Building spectre — AI-powered inventory/operations dashboard", 9),
    ("fact", "Spectre stack: React+Vite frontend, FastAPI backend, Ollama AI, SQLite+ChromaDB", 7),
    ("fact", "Building conduit — multi-provider AI chat system (this project)", 8),

    # === PEOPLE (importance 7-9) ===
    ("person", "Ken — Joseph's boss, engaged, takes data seriously", 9),
    ("person", "Heather — Joseph's other report-to, dismissive", 7),
    ("person", "Eric — colleague aligned on data-driven operations", 8),
    ("person", "Kyle — on-site staff at LM100, sometimes misses inventory entries", 7),

    # === VENDORS ===
    ("fact", "Gordon Food Service (GFS) — primary food distributor, largest volume", 7),
    ("fact", "Vistar Corporation — snacks/market items, dual-catalog issue (East + Mid-Atlantic)", 7),
    ("fact", "Penn-Del Baking — daily bread delivery, EXPENSED ON RECEIPT", 7),
    ("fact", "Kegel's Produce — fresh produce, EXPENSED ON RECEIPT", 7),
    ("fact", "Farmer Brothers Co — coffee/beverages vendor", 6),
    ("fact", "Pepsi (Bottling Group) and Coca-Cola are separate beverage vendors", 6),

    # === SCHEDULE ===
    ("schedule", "LM100 is a weekday corporate dining site — no weekend service", 7),
    ("schedule", "Inventory counts happen at period end for food cost calculation", 8),
    ("schedule", "Fiscal weeks can create stub periods — sales sometimes lumped across days", 7),

    # === PREFERENCES ===
    ("preference", "Prefers data-driven operational decisions over gut feelings", 9),
    ("preference", "Values automation — building tools to replace manual spreadsheet work", 9),
    ("preference", "Concise communication — doesn't like fluff or corporate speak", 8),
    ("preference", "Wants accurate food cost tracking above all else", 9),
    ("preference", "Prefers explanations with numbers and evidence", 8),

    # === TASKS/PROJECTS ===
    ("task", "Spectre: inventory template generation from IPS data + MOG cross-reference", 8),
    ("task", "Spectre: sales data ingestion — parse PDF reports into transcription sheets", 8),
    ("task", "Conduit: multi-provider AI assistant with tools, memory, heartbeats", 8),
    ("task", "Ongoing: keeping inventory template accurate against vendor catalog changes", 7),
    ("task", "Ongoing: daily sales entry into corporate back office portal", 7),

    # === TOPICS ===
    ("topic", "AI-powered food service operations and automation", 9),
    ("topic", "Inventory management and food cost optimization", 8),
    ("topic", "Building personal AI tools — spectre, conduit", 9),
    ("topic", "Python, FastAPI, React, Svelte, Swift development", 7),
    ("topic", "Data extraction from PDFs and vendor systems", 7),

    # === OPERATIONAL KNOWLEDGE ===
    ("fact", "Inventory location walking order: DRY → WALK-IN → FREEZER → MARKET → FRONT → KITCHEN → PAPER → CHEMICAL", 7),
    ("fact", "OrderMaestro requires ALL CAPS location names (FREEZER not Freezer)", 8),
    ("fact", "Items without GL codes that are on a vendor catalog = exclude from inventory", 8),
    ("fact", "Spices are EXPENSED ON RECEIPT — never count as inventory", 7),
    ("fact", "Vistar dual-catalog: 173 items in both East and Mid-Atlantic, causes upload flags", 7),
    ("fact", "IPS files use XML-encoded strings: _x0020_ for spaces", 6),
    ("fact", "Inflated previous inventory creates double-whammy on food cost calculation", 8),
]


async def main():
    await db.init_db()

    existing = await db.count_memories()
    print(f"Existing memories: {existing}")

    added = 0
    skipped = 0
    for category, content, importance in MEMORIES:
        if await db.find_duplicate_memory(content):
            skipped += 1
            continue
        await db.add_memory(
            category=category,
            content=content,
            source_conversation=None,
            importance=importance,
        )
        added += 1

    total = await db.count_memories()
    print(f"Added: {added}, Skipped (duplicates): {skipped}, Total memories: {total}")


if __name__ == "__main__":
    asyncio.run(main())
