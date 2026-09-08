# DER — Videoteca UNIFOR (T02.1)

Modelo de dados do sistema. 13 entidades cobrindo autenticação/perfis, acervo
audiovisual, empréstimos, reserva de salas, eventos, solicitações de professores
e notificações.

## Diagrama entidade-relacionamento

```mermaid
erDiagram
    users ||--|| profiles : "1:1 (id)"
    profiles ||--o{ reservations : faz
    profiles ||--o{ loans : faz
    profiles ||--o{ room_bookings : "reserva (professor)"
    profiles ||--o{ professor_requests : solicita
    profiles ||--o{ event_registrations : inscreve
    profiles ||--o{ notifications : recebe
    films ||--o{ copies : "tem cópias"
    films ||--o{ reservations : "é reservado"
    films ||--o{ events : "é exibido"
    copies ||--o{ loans : "é emprestada"
    rooms ||--o{ room_bookings : "é reservada"
    events ||--o{ event_registrations : "tem inscrições"

    users {
        uuid id PK
        text email UK
        text password_hash
        timestamptz created_at
    }
    profiles {
        uuid id PK,FK
        text full_name
        text role "aluno|professor|funcionario|admin"
        text registration
        text course
        text phone
    }
    films {
        bigint id PK
        text title
        int year
        text genre
        text status "available|inactive|maintenance"
        text internal_code UK
    }
    copies {
        bigint id PK
        bigint film_id FK
        text code UK
        text status "available|reserved|borrowed|maintenance|damaged"
    }
    reservations {
        bigint id PK
        uuid user_id FK
        bigint film_id FK
        text status "waiting|ready|fulfilled|cancelled|expired"
        timestamptz pickup_until
    }
    loans {
        bigint id PK
        uuid user_id FK
        bigint copy_id FK
        timestamptz borrowed_at
        timestamptz due_at
        timestamptz returned_at
        text status "borrowed|returned|late"
    }
    rooms {
        bigint id PK
        text name
        int capacity
        text[] equipment
        text status "available|inactive|maintenance"
    }
    room_bookings {
        bigint id PK
        bigint room_id FK
        uuid professor_id FK
        timestamptz starts_at
        timestamptz ends_at
        text status "pending|confirmed|cancelled"
    }
    events {
        bigint id PK
        bigint film_id FK
        timestamptz starts_at
        int slots
        int enrolled
        text status "draft|published|cancelled|finished"
    }
    event_registrations {
        bigint id PK
        bigint event_id FK
        uuid user_id FK
        boolean present
    }
    professor_requests {
        bigint id PK
        uuid professor_id FK
        text request_type
        jsonb payload
        text status "analysis|approved|refused"
    }
    notifications {
        bigint id PK
        uuid user_id FK
        text title
        boolean read
    }
    app_settings {
        int id PK "sempre = 1"
        int loan_days
        int max_items_per_user
        int max_reservations
        int reservation_pickup_days
    }
```

## Regras de integridade

| Regra | Onde | Migration |
|-------|------|-----------|
| `profiles.id` = `users.id` (1:1), cascade no delete | FK | 001 |
| Domínios de `status` restritos por `CHECK` | 8 tabelas | 002 |
| `room_bookings.ends_at > starts_at` | CHECK | 002 |
| `rooms.capacity > 0`, `films.year ∈ [1888,2100]` | CHECK | 002 |
| `loans.returned_at >= borrowed_at` | CHECK | 002 |
| **Anticonflito**: nenhuma sala com 2 reservas não-canceladas em horários sobrepostos | `EXCLUDE USING gist` | 003 |
| Índices em todas as FKs e colunas de filtro (RNF01) | 23 índices `idx_*` | 002 |

## Convenções

- **PKs de usuários**: `uuid` (compatível com futura federação/OAuth — T03).
- **PKs de domínio**: `bigint` identidade.
- **Timestamps**: `timestamptz` (sempre com fuso).
- `app_settings`: linha única (`CHECK id = 1`) com parâmetros globais do sistema.

## Migrations e seed

```
db/
├── migrations/
│   ├── 001_init_schema.sql          # tabelas base
│   ├── 002_constraints_indexes.sql  # CHECKs, validações e índices
│   └── 003_anticonflict.sql         # EXCLUDE constraint (btree_gist)
├── seed/
│   └── seed_dev.sql                 # massa de teste (senha: videoteca123)
└── DER.md
```

Aplicação local: `docker compose up postgres` roda as 3 migrations + seed no boot.
Aplicação manual: rode os arquivos de `migrations/` em ordem; `seed/` é opcional (dev).
