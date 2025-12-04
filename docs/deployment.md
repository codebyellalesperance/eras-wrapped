# deployment guide: render

this guide explains how to deploy tasteswipe to render.com as a free web service.

## prerequisites

1.  a [render.com](https://render.com) account.
2.  this repository pushed to github.
3.  your spotify developer credentials.
4.  your openai api key.

## deployment steps

1.  **dashboard**: log in to render and click **"new +"** -> **"web service"**.
2.  **connect repo**: select your `eras-wrapped` repository.
3.  **configure**:
    -   **name**: `tasteswipe` (or your preferred name)
    -   **region**: choose the one closest to you (e.g., ohio, frankfurt)
    -   **branch**: `main`
    -   **root directory**: `.` (leave empty)
    -   **runtime**: `python 3`
    -   **build command**: `pip install -r backend/requirements.txt`
    -   **start command**: `cd backend && gunicorn app:app`
4.  **environment variables**:
    add the following variables in the "environment" tab:
    -   `FLASK_ENV`: `production`
    -   `SPOTIFY_CLIENT_ID`: (from your spotify dashboard)
    -   `SPOTIFY_CLIENT_SECRET`: (from your spotify dashboard)
    -   `OPENAI_API_KEY`: (your openai key)
    -   `SECRET_KEY`: (generate a random string)
    -   `SPOTIFY_REDIRECT_URI`: `https://<your-app-name>.onrender.com/auth/callback`
    -   `FRONTEND_URL`: `https://<your-app-name>.onrender.com`
    -   `ALLOWED_ORIGINS`: `https://<your-app-name>.onrender.com`

5.  **deploy**: click **"create web service"**.

## post-deployment

1.  **update spotify dashboard**:
    -   go to [developer.spotify.com](https://developer.spotify.com/dashboard)
    -   edit your app settings.
    -   add your new redirect uri: `https://<your-app-name>.onrender.com/auth/callback`
    -   save changes.

2.  **verify**:
    -   visit your render url.
    -   try logging in with spotify.
    -   check `/health` endpoint.

## troubleshooting

-   **logs**: check the "logs" tab in render if the deployment fails.
-   **redirect uri mismatch**: ensure the uri in render env vars matches *exactly* what is in the spotify dashboard.
-   **cors errors**: ensure `FRONTEND_URL` and `ALLOWED_ORIGINS` are set correctly without trailing slashes.
