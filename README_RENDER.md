# CTF op Render — stappenplan

## 1) Maak een gratis account op Render
- Ga naar https://render.com en log in (GitHub is handig).

## 2) Gebruik deze map via GitHub of upload
- Optie A: Zet de map `ctf-render/` in een nieuwe GitHub-repo en kies **New → Blueprint** in Render.
- Optie B: Klik in Render op **New → Blueprint** en selecteer je repo met `render.yaml`.

## 3) Deploy
- Render maakt automatisch een webservice met een **/data** schijf voor je SQLite database.
- Na deployment krijg je een URL zoals `https://crypto-ctf.onrender.com`.

## 4) Teams & challenges
- Pas `app/seed_teams.json` en `app/seed_challenges.json` aan vóór deploy.
- De app initialiseert de database automatisch bij eerste start (als leeg).

## 5) Domein koppelen (aanrader: ctf.henniphof.com)
- Bij mijndomein.nl → **DNS** → voeg een **CNAME** record toe:
  - **Naam**: `ctf`
  - **Type**: CNAME
  - **Doel**: de Render-hostname van je service (bijv. `crypto-ctf.onrender.com`)
- Wacht 5–20 minuten.

> Let op: `henniphof.com/ctf/` is niet haalbaar zonder reverse proxy. Gebruik `ctf.henniphof.com`.

## 6) Admin
- Nieuw team (met joinkode terug):
```
curl -X POST https://ctf.henniphof.com/admin/add-team   -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json"   -d '{"name":"Nieuw Team"}'
```
- Challenge aan/uit:
```
curl -X POST https://ctf.henniphof.com/admin/activate   -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json"   -d '{"challenge_id":2,"active":false}'
```

## 7) Join codes bekijken (Render Shell → Bash)
```
sqlite3 /data/ctf.sqlite 'SELECT name, join_code FROM teams;'
```
