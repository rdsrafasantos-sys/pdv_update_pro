# Kanban — Painel Multi-Tenant (Redes / Unidades / RBAC / Redesign)

> O board de verdade vive no GitHub Projects (drag-and-drop, issues
> reais, histórico de mudanças). Este arquivo é só um ponteiro + legenda.

## 📋 Board

**https://github.com/users/rdsrafasantos-sys/projects/1**

5 colunas: `Backlog` → `A Fazer` → `Em Andamento` → `Em Revisão` → `Concluído`.

## 🏷️ Labels (uma por fase)

| Label | Fase |
|---|---|
| `fase-0-seguranca` | Fundamentos de segurança & dados (login, 2FA, auditoria, criptografia de segredos) |
| `fase-1-rbac` | Usuários & Perfis (RBAC) |
| `fase-2-redes` | Unidades & Redes via painel (substitui `.env` manual) |
| `fase-3-multitenant` | Refatoração multi-tenant do core (`discovery.py`/`dispatch.py`/etc.) |
| `fase-4-visual` | Redesign visual (aplicar `design/preview/`) |
| `fase-5-infra` | Infra para produção em nuvem |

## Como isso é mantido

Cada tarefa é uma *issue* do repositório, adicionada ao board. Conforme a
gente avança numa sessão, eu movo a issue de coluna (`gh project item-edit`)
e fecho quando concluída — não edito mais checkbox aqui, o board é a fonte
da verdade. Pra ver o estado atual rápido sem abrir o navegador:

```bash
gh project item-list 1 --owner rdsrafasantos-sys
```

Protótipo visual aprovado (referência da Fase 4): `design/preview/`.
