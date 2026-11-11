# Cómo publicar el proyecto

## 1. Probar en tu laptop
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # y pega tu clave en .env
streamlit run app/app.py
python -m pytest -q                # pruebas
```

## 2. Subir a GitHub (repo público)
Haz varios commits a medida que avanzas, no uno solo al final:
```bash
git init
git add backend tests data/reglas && git commit -m "Motor de plazos en días hábiles con pruebas"
git add ai && git commit -m "Extracción de resoluciones con IA y asistente con herramientas"
git add app .streamlit && git commit -m "App en Streamlit: tablero, mapa, línea de tiempo"
git add . && git commit -m "README, documentación y datos de demostración"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/plazos-tarifarios.git
git push -u origin main
```
Antes de subir, comprueba que no hay secretos: `git log -p | grep -iE "api_key|secret|token"`
(solo deben aparecer los nombres vacíos de `.env.example`).

## 3. Publicar en Streamlit Community Cloud (gratis)
1. Entra a share.streamlit.io con tu cuenta de GitHub.
2. «Create app» → elige el repo, rama `main`, archivo `app/app.py`.
3. En «Advanced settings → Secrets» pega:
   ```toml
   GEMINI_API_KEY = "tu-clave"
   ```
4. Publica y copia la URL pública para el README y el issue #204.

Pon un tope de gasto en la consola de tu proveedor de IA. La clave nunca va en el código.
