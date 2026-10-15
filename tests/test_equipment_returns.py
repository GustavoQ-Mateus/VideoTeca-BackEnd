import asyncio
from datetime import datetime, timedelta, timezone


async def test_checkout_return_damage_and_no_duplicate_return(api):
    client, pool, headers = api
    h = headers("funcionario")
    item = (
        await client.post(
            "/api/v1/staff/equipment",
            json={"name": "Camera", "category": "Video", "quantity": 2},
            headers=h,
        )
    ).json()
    body = {
        "equipment_id": item["id"],
        "registration": "aluno",
        "quantity": 2,
        "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    attempts = await asyncio.gather(
        *[
            client.post("/api/v1/staff/equipment/loans", json=body, headers=h)
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in attempts) == [201, 409]
    loan = next(r.json() for r in attempts if r.status_code == 201)
    path = f"/api/v1/staff/equipment/loans/{loan['id']}/return"
    assert (
        await client.post(
            path,
            json={"condition": "damaged", "damaged_quantity": 3, "notes": "Dano"},
            headers=h,
        )
    ).status_code == 422
    assert (
        await client.post(
            path,
            json={"condition": "damaged", "damaged_quantity": 1, "notes": ""},
            headers=h,
        )
    ).status_code == 422
    payload = {"condition": "damaged", "damaged_quantity": 1, "notes": "Lente quebrada"}
    attempts = await asyncio.gather(
        *[client.post(path, json=payload, headers=h) for _ in range(2)]
    )
    assert sorted(r.status_code for r in attempts) == [200, 409]
    items = (await client.get("/api/v1/staff/equipment", headers=h)).json()
    assert items[0]["available"] == 1
    assert items[0]["damaged_quantity"] == 1
    assert (await client.get("/api/v1/staff/equipment/incidents", headers=h)).json()[0][
        "damaged_quantity"
    ] == 1
    assert (
        await client.post(path, json=payload, headers=headers("aluno"))
    ).status_code == 403


async def test_late_loan_remains_late_after_return(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute(
        "INSERT INTO equipment(id,name,category,quantity) VALUES(1,'Projetor','Video',1)"
    )
    loan = await pool.fetchval(
        """INSERT INTO equipment_loans(equipment_id,user_id,quantity,checked_out_by,borrowed_at,due_at)
       SELECT 1,id,1,id,NOW()-INTERVAL '3 days',NOW()-INTERVAL '1 day' FROM profiles WHERE role='funcionario' RETURNING id"""
    )
    assert (await client.get("/api/v1/staff/equipment/incidents", headers=h)).json()[0][
        "late"
    ]
    assert (
        await client.post(
            f"/api/v1/staff/equipment/loans/{loan}/return",
            json={"condition": "good"},
            headers=h,
        )
    ).status_code == 200
    row = (await client.get("/api/v1/staff/equipment/incidents", headers=h)).json()[0]
    assert row["late"] and row["returned_at"]
