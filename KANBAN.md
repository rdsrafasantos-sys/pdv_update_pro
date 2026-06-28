# Kanban — Painel Multi-Tenant (Redes / Unidades / RBAC / Redesign)

> Acompanhamento das fases da reestruturação do `pdv-server` em um painel
> central multi-tenant (Unidade → Rede → Loja → PDV), com login/RBAC e novo
> visual. Atualizado a cada sessão — não editar manualmente o histórico de
> "Concluído", só mover cards entre colunas.

Protótipo visual aprovado em `design/preview/` (login, redes, dashboard de
rede, usuários/perfis) — referência de direção pra todo o trabalho visual
abaixo.

---

## 🟦 Backlog

### Fase 0 — Fundamentos de segurança & dados
- [ ] Modelo de dados: `Unidade`, `Rede`, `Loja`, `PDV` como entidades reais (hoje "rede" só existe como `.env` por deployment)
- [ ] Login com sessão segura (cookie `httponly`+`secure`+`samesite`, expiração/renovação)
- [ ] Hash de senha com Argon2id
- [ ] 2FA (TOTP)
- [ ] Rate limit no login (anti força-bruta)
- [ ] Log de auditoria (quem fez o quê, quando — toda ação sensível)
- [ ] Segredos por rede (Mongo URI, token, Tailscale Site ID) criptografados em repouso

### Fase 1 — Usuários & Perfis (RBAC)
- [ ] CRUD de Usuários
- [ ] CRUD de Perfis (permissões reutilizáveis, não codificadas no usuário)
- [ ] Permissão escopada por Unidade e/ou por Rede específica
- [ ] Tela "Usuários" + "Perfis" (baseado no protótipo)

### Fase 2 — Unidades & Redes (cadastro via painel)
- [ ] CRUD de Unidades
- [ ] CRUD de Redes pela tela (substitui editar `.env` manualmente)
- [ ] Tela "Redes" como nova home (cards por rede, agrupado por Unidade)

### Fase 3 — Refatoração multi-tenant do core
- [ ] `discovery.py` / `dispatch.py` / `replication.py` / `erp_db.py` / `integrador.py` parametrizados por rede (sem globais fixas)
- [ ] Rotas `/api/<rede_id>/...`
- [ ] Migrar dados/configs existentes (TEST, BONNA, produção real do PDV-215) pro novo modelo, sem perder histórico

### Fase 4 — Redesign visual (aplicar o protótipo aprovado)
- [ ] Novo design system em `app.css` (substitui o tema atual)
- [ ] Sidebar/topbar novos, com indicador fixo de Unidade/Rede ativa
- [ ] Redesenhar páginas existentes (Dashboard, Atualização de Agente/PDV, Check Replicação, Configurações) no novo visual

### Fase 5 — Infra para produção em nuvem
- [ ] Deploy do painel central único (substitui "uma instalação por cliente")
- [ ] Servidor central com múltiplas tags Tailscale (uma por rede atendida)
- [ ] HTTPS/TLS na frente do painel
- [ ] Backup do banco de configuração (usuários, redes, auditoria)

---

## 🟨 Em Andamento

_(vazio)_

---

## 🟩 Concluído

- [x] Protótipo visual aprovado (`design/preview/`: login, redes, dashboard de rede, usuários/perfis)
- [x] Decisão de arquitetura: hierarquia `Unidade → Rede → Loja → PDV`, multi-usuário com permissão por rede, cadastro de rede via formulário
