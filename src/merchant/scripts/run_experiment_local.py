import asyncio
import uuid
import random
from datetime import datetime, timedelta, UTC
from sqlmodel import Session, select
from src.merchant.db.database import get_engine, init_and_seed_db
from src.merchant.db.models import CheckoutSession, Product, CheckoutStatus

async def run_experiment():
    print("Running VyapaarOS Synthetic Revenue Experiment (Local DB only)...")
    init_and_seed_db()
    engine = get_engine()
    
    with Session(engine) as db:
        products = db.exec(select(Product)).all()
        if not products:
            print("No products found in DB. Run seed scripts first.")
            return

        print("Generating 100 Baseline Sessions (No Agent)...")
        for i in range(100):
            session_id = f"test_baseline_{uuid.uuid4()}"
            product = random.choice(products)
            qty = random.randint(1, 2)
            amount = product.base_price * qty
            
            days_ago = random.randint(3, 10)
            created_at = datetime.now(UTC) - timedelta(days=days_ago)
            
            session = CheckoutSession(
                id=session_id,
                currency="INR",
                status=CheckoutStatus.COMPLETED,
                line_items_json=f'[{{"id": "{product.id}", "quantity": {qty}, "name": "{product.name}", "price": {product.base_price}, "type": "physical"}}]',
                totals_json=f'[{{"type": "subtotal", "amount": {amount}}}, {{"type": "total", "amount": {amount}}}]',
                messages_json="[]",
                fulfillment_options_json="[]",
                metadata_json='{"agent_assisted": false}'
            )
            session.created_at = created_at
            session.updated_at = created_at + timedelta(minutes=5)
            db.add(session)
            
        print("Generating 100 Agent-Assisted Sessions (Simulated Agent)...")
        for i in range(100):
            session_id = f"test_agent_{uuid.uuid4()}"
            product = random.choice(products)
            
            items = []
            qty = 1
            items.append(f'{{"id": "{product.id}", "quantity": {qty}, "name": "{product.name}", "price": {product.base_price}, "type": "physical"}}')
            amount = product.base_price

            if random.random() < 0.40:
                upsell_candidates = [p for p in products if p.id != product.id]
                if upsell_candidates:
                    upsell_product = random.choice(upsell_candidates)
                    items.append(f'{{"id": "{upsell_product.id}", "quantity": 1, "name": "{upsell_product.name}", "price": {upsell_product.base_price}, "type": "physical"}}')
                    amount += upsell_product.base_price

            discount = int(amount * 0.1) if random.random() < 0.5 else 0
            
            final_amount = amount - discount
            
            days_ago = random.randint(0, 2)
            created_at = datetime.now(UTC) - timedelta(days=days_ago)
            
            session = CheckoutSession(
                id=session_id,
                currency="INR",
                status=CheckoutStatus.COMPLETED,
                line_items_json=f'[{",".join(items)}]',
                totals_json=f'[{{"type": "subtotal", "amount": {amount}}}, {{"type": "discount", "amount": -{discount}}}, {{"type": "total", "amount": {final_amount}}}]',
                messages_json="[]",
                fulfillment_options_json="[]",
                metadata_json='{"agent_assisted": true}'
            )
            session.created_at = created_at
            session.updated_at = created_at + timedelta(minutes=5)
            db.add(session)
            
        # Add retry loop for db locks
        for attempt in range(5):
            try:
                db.commit()
                break
            except Exception as e:
                db.rollback()
                print(f"DB lock retry {attempt}...")
                import time
                time.sleep(1)

        print("Successfully generated 200 synthetic checkout sessions.")
        
        # Calculate metric
        baseline = db.exec(select(CheckoutSession).where(CheckoutSession.id.like("test_baseline_%"))).all()
        agent = db.exec(select(CheckoutSession).where(CheckoutSession.id.like("test_agent_%"))).all()
        
        def get_total(s):
            import json
            totals = json.loads(s.totals_json)
            for t in totals:
                if t["type"] == "total":
                    return t["amount"]
            return 0
            
        base_aov = sum(get_total(s) for s in baseline) / len(baseline)
        agent_aov = sum(get_total(s) for s in agent) / len(agent)
        
        print(f"Baseline AOV: ₹{base_aov/100:.2f}")
        print(f"Agent AOV: ₹{agent_aov/100:.2f}")
        print(f"Revenue Uplift: {((agent_aov/base_aov) - 1)*100:.1f}%")

if __name__ == "__main__":
    asyncio.run(run_experiment())
