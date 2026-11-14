# Entrega das sprints 3–5

Sprint 3: apenas Terraform, documentação em `infra/sprint3/README.md`.
Sprint 4: disponibilidade por data/capacidade/recursos e reserva atômica de salas.
Sprint 5: circulação de equipamentos, filmes com cartaz, sessões e publicação.

## Banco e API

Instale `requirements.txt`. Em banco novo, execute migrations 001 a 008 em ordem; em banco existente com 001–003 aplicadas, execute 004 a 008. Execute cada arquivo em uma transação e interrompa no primeiro erro. As migrations também podem ser reaplicadas: não removem dados existentes. O usuário de migração precisa poder criar extensões, tabelas, funções e triggers. Use usuário distinto com acesso mínimo para a API.

As sessões publicadas geram ocupações em `room_bookings`, sujeitas à mesma exclusão GiST que as reservas. Rascunhos não bloqueiam salas; publicar ou editar uma sessão publicada revalida conflitos. Despublicar libera o horário. Períodos são semiabertos: o fim de uma reserva pode coincidir com o início de outra.

Reservas de professor ficam `pending`, bloqueiam o período e criam a solicitação para análise na mesma transação. A aprovação confirma; a recusa cancela a ocupação. Intervalos exigem fuso horário, início futuro e duração até 24 horas. A matrícula vem do usuário autenticado. O cliente envia datas com offset local convertido para UTC.

Equipamentos são distintos dos exemplares de filmes. `equipment_loans` mantém quem retirou/devolveu, quantidades, prazo, estado e observações. Estoque é calculado descontando empréstimos ativos e unidades avariadas. Devolução repetida é rejeitada. Quantidades avariadas ficam indisponíveis; a baixa de manutenção não faz parte desta sprint.

Cartazes aceitam até 5 MB e 20 megapixels. São decodificados e regravados como JPEG sem metadados, armazenados no banco. O endpoint público só exibe cartazes de filmes ativos. A exclusão é lógica e exige despublicar as sessões futuras do filme.

## Testes

```sh
pip install -r requirements-dev.txt
# Use Postgres local exclusivo com nome terminado em _test.
# Exemplo: TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55435/videoteca_test
python -m pytest -q
```

Os testes criam e removem somente schemas aleatórios `test_*`. Cobrem concorrência, autorização, capacidade, intervalos, conflitos entre reserva/sessão, cartaz inválido/grande, estoque e devolução duplicada. Não apontar para banco de produção.

## Integração Git

`feature/<tarefa>-<pessoa>` → PR para `developer` → PR de release para `main`.
Os pacotes dos membros são complementares. Valide a composição completa antes da release. PRs de módulos ainda dependentes devem permanecer em rascunho. `main` continua sendo a branch padrão: fechamento por `Closes` ocorre quando o PR alcança essa branch, não ao entrar em `developer`.
