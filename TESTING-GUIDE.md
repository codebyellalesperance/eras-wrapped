# 🧪 Testing Guide - Spotify Eras

## Quick Start (2 Steps)

### Step 1: Create `.env` File
```bash
cp .env.example .env
# Then edit .env and add your OpenAI API key
```

**Your `.env` should look like:**
```
OPENAI_API_KEY=sk-your-actual-key-here
FLASK_ENV=development
ALLOWED_ORIGINS=*
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT=30
```

### Step 2: Run the Application
```bash
# Terminal 1: Start Backend
cd backend
python3 app.py
# Should show: Running on http://127.0.0.1:5000

# Terminal 2: Serve Frontend
cd frontend
python3 -m http.server 8000
# Open browser to: http://localhost:8000
```

---

## ✅ What's Already Complete

### Backend ✅
- All Phase 0-5 implemented
- Flask server with CORS
- File parsing (JSON/ZIP)
- Era segmentation
- LLM naming integration
- Playlist generation
- API endpoints ready

### Frontend ✅
- All Phase 6-9 implemented
- Premium UI/UX animations
- Drag-and-drop file upload
- SSE progress tracking
- Timeline with animated counters
- Era detail view
- Mobile responsive

---

## 🧪 Testing Checklist

### 1. **Backend Health Check**
Open: `http://localhost:5000/health`
- Should return: `{"status": "ok"}`

### 2. **Upload Test File**
You need Spotify data export:
- Go to https://www.spotify.com/account/privacy/
- Request "Account Data"
- Wait for email (1-30 days)
- Upload the `StreamingHistory*.json` files

### 3. **Test with Sample** (If you have one)
- Drag and drop JSON/ZIP file
- Click "Analyze My Music"
- Watch progress bar (should show stages)
- View timeline with animated counters
- Click an era to see details
- Copy playlist to clipboard

---

## 🐛 Common Issues & Fixes

### Issue: "ModuleNotFoundError"
**Fix:** Install dependencies
```bash
cd backend
pip3 install -r requirements.txt
```

### Issue: "OPENAI_API_KEY not set"
**Fix:** Make sure `.env` file exists and has your key
```bash
cat .env  # Should show your API key
```

### Issue: "CORS error in browser"
**Fix:** Make sure `ALLOWED_ORIGINS=*` in `.env`

### Issue: Frontend can't connect to backend
**Fix:** 
1. Check backend is running on port 5000
2. Check frontend `app.js` has:
   ```javascript
   const API_URL = 'http://localhost:5000'
   ```

### Issue: Port 5000 already in use
**Fix:** Change port in backend/app.py:
```python
app.run(debug=True, port=5001)  # Use 5001
```
And update frontend API_URL

---

## 📊 Expected Behavior

### **Landing Page**
- ✅ Upload area breathes on hover
- ✅ Drag-drop shows green overlay
- ✅ File info slides in with bounce
- ✅ Button glows on hover
- ✅ File validation (type, size)

### **Processing View**
- ✅ Progress bar fills smoothly
- ✅ Stage text fades when changing
- ✅ Progress never goes backwards
- ✅ SSE updates in real-time
- ✅ Spinner rotates continuously

### **Timeline View**
- ✅ Numbers count up from 0
- ✅ Era cards stagger in from left
- ✅ Cards slide right + glow on hover
- ✅ Timeline dots pulse on hover
- ✅ Artist tags turn green on hover

### **Detail View**
- ✅ Loading spinner during fetch
- ✅ Smooth content reveal
- ✅ Artists listed with plays
- ✅ Tracks numbered with artists
- ✅ Copy button shows toast

---

## 🎯 Testing Scenarios

### **Scenario 1: Small File (< 1MB)**
1. Upload small JSON file
2. Processing should take ~10-20 seconds
3. Should see 2-5 eras
4. Timeline should load instantly

### **Scenario 2: Large File (100MB+)**
1. Upload large ZIP file
2. Processing may take 1-2 minutes
3. Should see 10+ eras
4. Watch for timeout (5 min client-side)

### **Scenario 3: Error Recovery**
1. Upload invalid file (.txt)
2. Should see error message with shake
3. Upload valid file
4. Should work normally

### **Scenario 4: Network Interruption**
1. Start processing
2. Kill backend server
3. Should see "Connection lost" error
4. Click "Try Again"
5. Should reset to landing page

---

## 🚀 Next Steps After Testing

If everything works locally:

### **1. Deployment Options**

**Frontend:**
- Vercel (recommended)
- Netlify
- GitHub Pages

**Backend:**
- Railway (recommended)
- Render
- Heroku

### **2. Environment Updates**
Update frontend `app.js` for production:
```javascript
const API_URL = window.location.hostname === 'localhost'
    ? 'http://localhost:5000'
    : 'https://your-backend-url.railway.app';
```

### **3. Optional Enhancements**
- Add sample data for demo
- Add share to social media
- Add confetti on timeline reveal
- Add export as PDF
- Add dark/light mode toggle

---

## 📝 Need Spotify Data?

If you don't have Spotify data yet:

**Option 1:** Request real data
- Go to: https://www.spotify.com/account/privacy/
- Click "Download your data"
- Select "Account data"
- Wait 1-30 days for email
- Upload the files

**Option 2:** Create sample data (for testing)
- Create a small JSON file with mock listening history
- Follow the Spotify JSON format
- Upload and test the flow

---

## ✨ You're All Set!

The app is **100% complete**! Just need to:
1. ✅ Add your OpenAI API key to `.env`
2. ✅ Run backend server
3. ✅ Serve frontend
4. ✅ Upload Spotify data
5. ✅ Enjoy the premium experience!

**Questions to answer:**
- Do you have your OpenAI API key?
- Do you have Spotify data to test with?
- Should I help you create sample data for testing?
