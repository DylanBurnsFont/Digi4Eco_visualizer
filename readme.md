# Marine Observation Editor - Advanced Features Guide

## Overview

The **Advanced Marine Observation Editor** allows you to:
- ✅ View and browse marine observation images with detections
- ✅ **Edit existing detections** (change species, confidence, verification status)
- ✅ **Add new detections** by drawing bounding boxes on images
- ✅ **Delete incorrect detections**
- ✅ **Save all changes back to NetCDF files**

This is perfect for data validation, correction, and annotation workflows.

---

## Installation & Setup

### Prerequisites
```bash
pip install -r requirements.txt
```

### Start the Server
```bash
python3 web_server.py
```

You should see:
```
============================================================
Marine Observation Editor - Advanced
============================================================

✓ Starting server...

📱 Open your browser and go to:

   http://localhost:5000
```

Then open **http://localhost:5000** in your browser.