"""Gera db/seed/seed_dev_tmdb.sql: acervo de demonstração com metadados e cartazes do TMDB.

Uso (o token nunca é versionado nem vai para a AWS):
    TMDB_READ_TOKEN=... python scripts/tmdb_catalog.py
O SQL resultante só guarda dados públicos (títulos, sinopses, URLs de cartaz em image.tmdb.org)
e roda depois de seed_dev.sql em `python -m app.migrate --seed`.
This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "db/seed"
OUT = SEED / "seed_00_catalogo_tmdb.sql"  # antes do seed_dev.sql
EXTRAS = SEED / "seed_zz_programacao_tmdb.sql"  # depois do seed_dev.sql
IMG = "https://image.tmdb.org/t/p/w500"
BACKDROP = "https://image.tmdb.org/t/p/w1280"
# Acervo simulado de uma videoteca universitária: cinema brasileiro e clássicos de estudo.
FILMS = [
    ("Cidade de Deus", 2002), ("Central do Brasil", 1998), ("Parasita", 2019),
    ("O Auto da Compadecida", 2000), ("2001: Uma Odisseia no Espaço", 1968), ("Bacurau", 2019),
    ("Deus e o Diabo na Terra do Sol", 1964), ("Vidas Secas", 1963), ("O Pagador de Promessas", 1962),
    ("Terra em Transe", 1967), ("Pixote: A Lei do Mais Fraco", 1980), ("Eles Não Usam Black-Tie", 1981),
    ("O Som ao Redor", 2012), ("Aquarius", 2016), ("Que Horas Ela Volta?", 2015),
    ("Tropa de Elite", 2007), ("Abril Despedaçado", 2001), ("Lavoura Arcaica", 2001),
    ("Madame Satã", 2002), ("O Céu de Suely", 2006), ("Cabra Marcado para Morrer", 1984),
    ("Ainda Estou Aqui", 2024), ("A Vida Invisível", 2019), ("Marte Um", 2022),
    ("Cidadão Kane", 1941), ("Um Corpo que Cai", 1958), ("Os Incompreendidos", 1959),
    ("Acossado", 1960), ("8½", 1963, 422), ("Rashomon", 1950), ("Era uma Vez em Tóquio", 1953),
    ("O Encouraçado Potemkin", 1925), ("Metrópolis", 1927), ("Ladrões de Bicicleta", 1948),
    ("Persona", 1966), ("Amor à Flor da Pele", 2000), ("A Viagem de Chihiro", 2001),
    ("Roma", 2018), ("Moonlight: Sob a Luz do Luar", 2016), ("Retrato de uma Jovem em Chamas", 2019),
]
MEDIA = ["Blu-ray", "DVD", "Digital"]


def api(path, **params):
    query = urllib.parse.urlencode({"language": "pt-BR", **params})
    req = urllib.request.Request(
        f"https://api.themoviedb.org/3{path}?{query}",
        headers={"Authorization": "Bearer " + os.environ["TMDB_READ_TOKEN"], "accept": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=20))


def q(value):
    return "NULL" if value in (None, "") else "'" + str(value).replace("'", "''") + "'"


def main():
    rows = []
    for n, (title, year, *tmdb_id) in enumerate(FILMS, start=1):
        if not tmdb_id:  # busca por título/ano; um ID fixo resolve homônimos
            found = api("/search/movie", query=title, year=year).get("results") or api("/search/movie", query=title).get("results")
            if not found:
                print("não encontrado:", title, file=sys.stderr)
                continue
            tmdb_id = [found[0]["id"]]
        m = api(f"/movie/{tmdb_id[0]}", append_to_response="credits")
        director = ", ".join(c["name"] for c in m["credits"]["crew"] if c["job"] == "Director") or None
        cast = ", ".join(c["name"] for c in m["credits"]["cast"][:4])
        rows.append({
            "code": f"VT-{n:04d}", "title": title, "original": m.get("original_title"),
            "year": (m.get("release_date") or str(year))[:4], "director": director,
            "genre": (m.get("genres") or [{"name": None}])[0]["name"],
            "duration": f"{m['runtime']} min" if m.get("runtime") else None,
            "language": (m.get("spoken_languages") or [{"name": None}])[0].get("name"),
            "country": (m.get("production_countries") or [{"name": None}])[0]["name"],
            "cover": IMG + m["poster_path"] if m.get("poster_path") else None,
            "backdrop": BACKDROP + m["backdrop_path"] if m.get("backdrop_path") else None,
            "synopsis": (m.get("overview") or "")[:1200], "cast": cast,
            "media": MEDIA[n % 3], "shelf": f"Estante {chr(65 + (n - 1) // 8)}{(n - 1) % 8 + 1}",
            "status": "maintenance" if n % 13 == 0 else "available",
        })
        print("ok", title, flush=True)

    lines = [
        "-- Gerado por scripts/tmdb_catalog.py — acervo SIMULADO para demonstração.",
        "-- Metadados e cartazes: TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.",
        "-- Idempotente: atualiza filmes pelo código de tombo (internal_code).",
    ]
    for r in rows:
        lines.append(
            "INSERT INTO films (id,internal_code,title,original_title,year,director,genre,duration,language,rating,media,cover,status,location,synopsis,\"cast\",country,"
            "rights_status,rights_holder,license_type,rights_valid_until,rights_notes,backdrop) VALUES ("
            + ",".join([str(int(r["code"][3:])), q(r["code"]), q(r["title"]), q(r["original"]), str(int(r["year"])), q(r["director"]), q(r["genre"]),
                        q(r["duration"]), q(r["language"]), "'Livre'", q(r["media"]), q(r["cover"]), q(r["status"]), q(r["shelf"]),
                        q(r["synopsis"]), q(r["cast"]), q(r["country"]),
                        "'authorized'", "'Licença institucional (simulada)'", "'exibicao_publica'",
                        "(NOW() + INTERVAL '1 year')::date", "'Acervo de demonstração'", q(r["backdrop"])])
            + ") ON CONFLICT (internal_code) DO UPDATE SET cover=EXCLUDED.cover, backdrop=EXCLUDED.backdrop, synopsis=EXCLUDED.synopsis, director=EXCLUDED.director,"
            " genre=EXCLUDED.genre, duration=EXCLUDED.duration, original_title=EXCLUDED.original_title, \"cast\"=EXCLUDED.\"cast\";"
        )
    lines.append("SELECT setval(pg_get_serial_sequence('films','id'), (SELECT MAX(id) FROM films));")
    # Comprovante fictício por filme autorizado (a publicação exige; trigger da migration 010).
    lines.append(
        "INSERT INTO film_rights_proofs (film_id, filename, media_type, content, sha256) "
        "SELECT f.id, 'licenca-' || f.internal_code || '.pdf', 'application/pdf', c, encode(sha256(c), 'hex') "
        "FROM films f CROSS JOIN LATERAL (SELECT convert_to('%PDF-1.4 licenca ficticia ' || f.internal_code, 'UTF8') AS c) s "
        "WHERE f.internal_code LIKE 'VT-%' AND f.rights_status = 'authorized' "
        "AND NOT EXISTS (SELECT 1 FROM film_rights_proofs p WHERE p.film_id = f.id);"
    )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["-- Gerado por scripts/tmdb_catalog.py — cópias, comprovantes e programação de demonstração (após seed_dev.sql)."]
    # Cópias físicas fictícias para os títulos novos (1 a 3 por filme), sem tocar nas do seed_dev.
    lines.append(
        "INSERT INTO copies (film_id, code, patrimonio, condition, status) "
        "SELECT f.id, f.internal_code || '-' || g, 'PAT-' || f.internal_code || '-' || g, 'good', 'available' "
        "FROM films f CROSS JOIN generate_series(1, 3) g "
        "WHERE f.internal_code LIKE 'VT-%' AND g <= 1 + (f.id % 3) AND NOT EXISTS (SELECT 1 FROM copies c WHERE c.film_id = f.id) "
        "ON CONFLICT (code) DO NOTHING;"
    )
    # Bases antigas gravaram um marcador visível na descrição; remove.
    lines.append("UPDATE events SET description = replace(description, ' [demo]', '') WHERE description LIKE '%[demo]%';")
    # Programação de demonstração: 2 sessões/dia por 7 dias no Mini Auditório (sala 3), relativas à data
    # de carga e no fuso de Fortaleza. Só insere uma vez.
    picks = ["VT-0007", "VT-0013", "VT-0025", "VT-0030", "VT-0022", "VT-0008", "VT-0027",
             "VT-0037", "VT-0014", "VT-0034", "VT-0018", "VT-0038", "VT-0026", "VT-0021"]
    values = []
    for i, code in enumerate(picks):
        day, evening = divmod(i, 2)
        hour = "19:00" if evening else "14:00"
        kind = "Cineclube" if evening and day % 2 == 0 else "Sessão"
        enrolled = [12, 80, 34, 5, 61, 80, 20, 47, 9, 73, 30, 80, 15, 52][i]
        values.append(f"({q(code)}, {day}, '{hour}', '{kind}', {enrolled})")
    lines.append(
        "INSERT INTO events (title, event_type, film_id, room_id, location, starts_at, ends_at, slots, enrolled, description, certificate, status) "
        "SELECT v.kind || ': ' || f.title, 'session', f.id, 3, 'Mini Auditório', s.starts_at, "
        "s.starts_at + make_interval(mins => COALESCE(NULLIF(regexp_replace(f.duration, '\\D', '', 'g'), '')::int, 120) + 30), "
        "80, v.enrolled, CASE WHEN v.kind = 'Cineclube' THEN 'Exibição seguida de debate.' ELSE 'Sessão do acervo.' END, "
        "v.kind = 'Cineclube', 'published' "
        "FROM (VALUES " + ", ".join(values) + ") AS v(code, day, hour, kind, enrolled) "
        "JOIN films f ON f.internal_code = v.code "
        "CROSS JOIN LATERAL (SELECT ((date_trunc('day', NOW() AT TIME ZONE 'America/Fortaleza') + make_interval(days => v.day + 1) "
        "+ v.hour::time) AT TIME ZONE 'America/Fortaleza') AS starts_at) s "
        "WHERE NOT EXISTS (SELECT 1 FROM events WHERE room_id = 3 AND event_type = 'session' AND description IN ('Sessão do acervo.', 'Exibição seguida de debate.'));"
    )
    EXTRAS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(len(rows), "filmes ->", OUT.name, "+", EXTRAS.name)


if __name__ == "__main__":
    main()
