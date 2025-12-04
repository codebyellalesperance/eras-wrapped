# project overview: tasteswipe (eras wrapped)

## executive summary
tasteswipe is a web application that gamifies music discovery. it connects to a user's spotify account, analyzes their listening history using ai to define their current "musical era," and offers a tinder-style swipe interface to discover new music and build playlists.

## architecture

### frontend
- **tech**: vanilla javascript, html5, css3.
- **rationale**: avoided heavy frameworks (react/vue) to keep the app lightweight, fast, and easy to debug. direct dom manipulation was sufficient for the swipe interface.
- **key components**:
  - `app.js`: handles all logic, state management, and api calls.
  - `index.css`: custom design system with glassmorphism aesthetics.
  - `swipe-cards`: custom implementation of tinder-like physics.

### backend
- **tech**: python, flask.
- **rationale**: flask provides a minimal, flexible core perfect for wrapping api calls and handling oauth without the overhead of django.
- **key components**:
  - `app.py`: main entry point, routes, and error handling.
  - `spotify_auth.py`: handles complex oauth flow and token management.
  - `spotify_service.py`: interfaces with spotify api for data and playlists.
  - `ai_service.py`: interfaces with openai to analyze taste profiles.

## key features & implementation

1.  **spotify oauth 2.0**:
    -   implemented secure authorization code flow.
    -   **success**: seamless login/logout experience.
    -   **pitfall**: token expiration after 1 hour caused session crashes.
    -   **solution**: implemented automatic token refresh logic in the backend middleware.

2.  **ai taste analysis**:
    -   uses openai gpt-4o-mini to analyze top artists/tracks.
    -   generates a creative "era name" (e.g., "sad girl autumn", "hyperpop chaos").
    -   **rationale**: adds a layer of personalization that raw data cannot provide.

3.  **swipe interface**:
    -   custom drag-and-drop logic for song cards.
    -   right swipe = like (add to playlist), left swipe = pass.
    -   **success**: feels native and responsive on mobile.

4.  **production hardening**:
    -   **gunicorn**: replaced flask dev server for robust concurrent request handling.
    -   **security**: added csp, hsts, secure cookies, and cors restrictions.
    -   **logging**: structured logging for easier debugging in production.

## challenges & pitfalls

### 1. session management
-   **challenge**: balancing client-side state (ui) with server-side security (tokens).
-   **pitfall**: storing tokens in localstorage is insecure (xss risk).
-   **solution**: moved all token storage to server-side http-only encrypted cookies. frontend only stores ui state.

### 2. cors & environment differences
-   **challenge**: frontend (port 8000) talking to backend (port 5000) caused cors errors.
-   **solution**: implemented strict cors configuration that adapts based on `FLASK_ENV` (dev vs prod).

### 3. spotify api limits
-   **challenge**: rate limiting and empty recommendations for new users.
-   **solution**: added fallback seeds (popular artists) and robust error handling for empty api responses.

## successes

-   **clean codebase**: achieved a highly organized structure with clear separation of concerns.
-   **test coverage**: reached ~94% test coverage on the backend, ensuring reliability.
-   **minimalist design**: the "no-fluff" aesthetic extends from the ui to the documentation.
-   **zero-dependency frontend**: the frontend runs on any static server without a build step, drastically simplifying deployment.

## future roadmap

-   **database**: migrate from in-memory sessions to redis/postgres for persistence.
-   **deployment**: push to a platform like railway or render.
-   **mobile app**: wrap the responsive web app into a native container.
