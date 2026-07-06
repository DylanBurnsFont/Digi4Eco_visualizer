"""
Allows users to view/edit detections and save changes to NetCDF files.
Includes image proxy to handle CORS issues.

Usage:
    pip install -r requirements.txt
    python3 web_server.py
"""

from flask import Flask, render_template_string, request, jsonify, send_file
import xarray as xr
import numpy as np
import requests as _requests
from datetime import datetime, timezone
import tempfile
import os
import json
import io
import traceback
import time

app = Flask(__name__)

# Server-side cache: maps file_id -> temp file path of the uploaded NetCDF
_netcdf_cache = {}

# WoRMS LSID cache
_worms_cache = {}
_worms_session = _requests.Session()

# Simple image proxy endpoint
@app.route('/api/proxy-image')
def proxy_image():
    """Proxy images using Range requests to handle server-side truncation"""
    url = request.args.get('url')
    if not url:
        return 'No URL provided', 400
    
    # First request: get initial data and find total size
    try:
        session = _requests.Session()
        session.trust_env = False
        
        import warnings
        warnings.filterwarnings('ignore')
        
        # Initial request to get size
        response = session.head(
            url,
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10,
            verify=False,
            allow_redirects=True
        )
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        supports_range = 'accept-ranges' in response.headers and response.headers['accept-ranges'].lower() != 'none'
        
        print(f"File size: {total_size} bytes, Range support: {supports_range}")
        
    except Exception as e:
        print(f"HEAD request failed: {e}")
        total_size = 0
        supports_range = False
    
    # Download the full image, using Range requests if supported
    image_data = bytearray()
    
    if supports_range and total_size > 0:
        # Use Range requests to fetch in chunks
        chunk_size = 100000
        for start in range(0, total_size, chunk_size):
            end = min(start + chunk_size - 1, total_size - 1)
            
            try:
                session = _requests.Session()
                session.trust_env = False
                
                response = session.get(
                    url,
                    headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Range': f'bytes={start}-{end}'
                    },
                    timeout=15,
                    verify=False,
                    allow_redirects=True
                )
                
                if response.status_code in [200, 206]:  # 206 = Partial Content
                    image_data.extend(response.content)
                    print(f"Range {start}-{end}: {len(response.content)} bytes")
                else:
                    print(f"Range request failed: {response.status_code}")
                    break
                    
            except Exception as e:
                print(f"Range request {start}-{end} failed: {e}")
                break
    else:
        # Fallback: standard request with retries
        for attempt in range(5):
            try:
                session = _requests.Session()
                session.trust_env = False
                
                response = session.get(
                    url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Connection': 'close'
                    },
                    timeout=30,
                    stream=False,
                    verify=False,
                    allow_redirects=True
                )
                response.raise_for_status()
                image_data = bytearray(response.content)
                
                if len(image_data) > 0:
                    print(f"✓ Got {len(image_data)} bytes on attempt {attempt + 1}")
                    break
                    
            except Exception as e:
                print(f"Attempt {attempt + 1}: {str(e)[:80]}")
                if attempt < 4:
                    time.sleep(1)
    
    if image_data:
        try:
            # Verify it's valid image data
            session = _requests.Session()
            response = session.head(url, timeout=5, verify=False)
            content_type = response.headers.get('content-type', 'image/jpeg')
            
            print(f"→ Returning {len(image_data)} bytes as {content_type}")
            return send_file(
                io.BytesIO(bytes(image_data)),
                mimetype=content_type
            )
        except Exception as e:
            print(f"Error getting content-type: {e}")
            return send_file(
                io.BytesIO(bytes(image_data)),
                mimetype='image/jpeg'
            )
    
    return 'Failed to load image', 500

# WoRMS species name suggestion endpoint
@app.route('/api/worms-lsid')
def worms_lsid():
    """Resolve an exact species name to its WoRMS LSID (two-step lookup)."""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'lsid': ''})
    if name in _worms_cache:
        return jsonify({'lsid': _worms_cache[name]})
    try:
        import urllib.parse
        encoded = urllib.parse.quote(name)
        aphia_url = f"https://www.marinespecies.org/rest/AphiaIDByName/{encoded}"
        aphia_id = _worms_session.get(aphia_url, timeout=5).json()
    except Exception:
        return jsonify({'lsid': ''})
    if not aphia_id:
        _worms_cache[name] = ''
        return jsonify({'lsid': ''})
    try:
        record_url = f"https://www.marinespecies.org/rest/AphiaRecordByAphiaID/{aphia_id}"
        record = _worms_session.get(record_url, timeout=5).json()
        lsid = str(record.get('lsid') or '')
    except Exception:
        lsid = ''
    _worms_cache[name] = lsid
    return jsonify({'lsid': lsid})

@app.route('/api/worms-suggest')
def worms_suggest():
    """Return up to 10 WoRMS records matching a partial species name."""
    query = request.args.get('q', '').strip()
    if len(query) < 3:
        return jsonify([])
    try:
        import urllib.parse
        encoded = urllib.parse.quote(query)
        url = (f"https://www.marinespecies.org/rest/AphiaRecordsByName/"
               f"{encoded}?like=true&marine_only=true&offset=1")
        r = _requests.get(url, timeout=5)
        if r.status_code != 200:
            return jsonify([])
        records = r.json() or []
        seen = set()
        results = []
        for rec in records:
            name = rec.get("scientificname", "")
            if name and name not in seen:
                seen.add(name)
                results.append({
                    "name": name,
                    "status": rec.get("status"),
                    "valid_name": rec.get("valid_name"),
                    "lsid": rec.get("lsid"),
                })
        return jsonify(results[:10])
    except Exception:
        return jsonify([])

# HTML/CSS/JS Template
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Marine Observation Editor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', 'Roboto', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #f1f5f9;
            min-height: 100vh;
            padding: 2rem;
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
        }

        .header {
            margin-bottom: 2rem;
        }

        h1 {
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, #60a5fa, #34d399);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .subtitle {
            color: #94a3b8;
            font-size: 0.95rem;
        }

        .main-grid {
            display: grid;
            grid-template-columns: 1fr 400px;
            gap: 2rem;
            margin-bottom: 2rem;
        }

        @media (max-width: 1200px) {
            .main-grid {
                grid-template-columns: 1fr;
            }
        }

        .controls-panel {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 0.75rem;
            padding: 1.5rem;
            margin-bottom: 2rem;
            backdrop-filter: blur(10px);
        }

        .controls-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
        }

        .control-group {
            display: flex;
            flex-direction: column;
        }

        .control-label {
            font-size: 0.875rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #e2e8f0;
        }

        button {
            padding: 0.625rem 1rem;
            border: none;
            border-radius: 0.5rem;
            cursor: pointer;
            font-weight: 500;
            font-size: 0.875rem;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }

        .btn-primary {
            background: linear-gradient(135deg, #3b82f6, #2563eb);
            color: white;
        }

        .btn-primary:hover {
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
        }

        .btn-secondary {
            background: rgba(100, 116, 139, 0.3);
            border: 1px solid rgba(148, 163, 184, 0.3);
            color: #e2e8f0;
        }

        .btn-secondary:hover {
            background: rgba(100, 116, 139, 0.5);
        }

        .btn-success {
            background: linear-gradient(135deg, #10b981, #059669);
            color: white;
        }

        .btn-success:hover {
            background: linear-gradient(135deg, #059669, #047857);
        }

        .btn-danger {
            background: linear-gradient(135deg, #ef4444, #dc2626);
            color: white;
        }

        .btn-danger:hover {
            background: linear-gradient(135deg, #dc2626, #b91c1c);
        }

        .btn-small {
            padding: 0.5rem 0.75rem;
            font-size: 0.75rem;
        }

        input[type="text"], input[type="file"], input[type="number"], select {
            width: 100%;
            padding: 0.625rem;
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid rgba(148, 163, 184, 0.3);
            border-radius: 0.5rem;
            color: #e2e8f0;
            font-size: 0.875rem;
        }

        input[type="checkbox"] {
            cursor: pointer;
            margin-right: 0.5rem;
        }

        .checkbox-label {
            display: flex;
            align-items: center;
            cursor: pointer;
            font-size: 0.875rem;
        }

        .canvas-section {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 0.75rem;
            overflow: hidden;
            position: relative;
        }

        .canvas-wrapper {
            display: flex;
            justify-content: center;
            align-items: center;
            background: #000;
            min-height: 500px;
            position: relative;
            padding: 1rem;
        }

        canvas {
            max-width: 100%;
            max-height: 700px;
            display: block;
            cursor: crosshair;
        }

        .canvas-instructions {
            position: absolute;
            top: 1rem;
            left: 1rem;
            background: rgba(0, 0, 0, 0.8);
            color: #94a3b8;
            padding: 1rem;
            border-radius: 0.5rem;
            font-size: 0.75rem;
            max-width: 250px;
            display: none;
        }

        .canvas-instructions.active {
            display: block;
        }

        .loading-spinner {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: #60a5fa;
            font-size: 0.875rem;
        }

        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .sidebar-panel {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 0.75rem;
            padding: 1rem;
            backdrop-filter: blur(10px);
        }

        .sidebar-title {
            font-size: 0.875rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: #60a5fa;
        }

        .detection-list {
            max-height: 400px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .detection-item {
            background: rgba(30, 41, 59, 0.5);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 0.5rem;
            padding: 0.75rem;
            cursor: pointer;
            transition: all 0.2s ease;
            font-size: 0.75rem;
        }

        .detection-item:hover {
            background: rgba(30, 41, 59, 0.8);
            border-color: rgba(96, 165, 250, 0.5);
        }

        .detection-item.selected {
            background: rgba(96, 165, 250, 0.2);
            border-color: #60a5fa;
        }

        .detection-name {
            font-weight: 600;
            color: #60a5fa;
        }

        .detection-conf {
            color: #94a3b8;
            font-size: 0.7rem;
        }

        .edit-panel {
            display: none;
        }

        .edit-panel.active {
            display: block;
        }

        .form-group {
            margin-bottom: 1rem;
        }

        .form-group label {
            display: block;
            font-size: 0.75rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #e2e8f0;
            text-transform: uppercase;
        }

        .button-group {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.5rem;
            margin-top: 1rem;
        }

        .message {
            padding: 1rem;
            border-radius: 0.5rem;
            margin-bottom: 1rem;
            font-size: 0.875rem;
            display: none;
        }

        .message.error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #ef4444;
        }

        .message.success {
            background: rgba(52, 211, 153, 0.1);
            border: 1px solid rgba(52, 211, 153, 0.3);
            color: #34d399;
        }

        .message.active {
            display: block;
        }

        .empty-state {
            text-align: center;
            padding: 3rem;
            background: rgba(30, 41, 59, 0.3);
            border-radius: 0.75rem;
            border: 2px dashed rgba(148, 163, 184, 0.2);
        }

        .stats {
            padding: 1rem;
            background: rgba(30, 41, 59, 0.3);
            border-radius: 0.5rem;
            font-size: 0.75rem;
            color: #94a3b8;
        }

        .stats p {
            margin: 0.5rem 0;
        }

        strong {
            color: #e2e8f0;
        }

        .nav-section {
            display: flex;
            gap: 1rem;
            margin-top: 1rem;
        }

        .nav-section button {
            flex: 1;
        }

        .file-format-info {
            font-size: 0.75rem;
            color: #64748b;
            margin-top: 0.25rem;
        }

        .worms-dropdown {
            position: absolute;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 4px;
            z-index: 1000;
            width: 100%;
            max-height: 200px;
            overflow-y: auto;
            top: 100%;
            left: 0;
        }
        .worms-item {
            padding: 6px 10px;
            cursor: pointer;
            font-size: 0.82rem;
            color: #e2e8f0;
            border-bottom: 1px solid #1e293b;
        }
        .worms-item:hover { background: #334155; }
        .worms-item .worms-synonym { color: #94a3b8; font-style: italic; }
        .worms-item .worms-badge {
            font-size: 0.68rem;
            padding: 1px 5px;
            border-radius: 3px;
            margin-left: 5px;
            background: #0f4c75;
            color: #7dd3fc;
        }
        .worms-badge.synonym { background: #422006; color: #fdba74; }
        .worms-section-header {
            padding: 3px 8px;
            font-size: 0.68rem;
            color: #64748b;
            background: #0f172a;
            border-bottom: 1px solid #334155;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            pointer-events: none;
        }

        /* Custom Modal Dialog */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            z-index: 2000;
            align-items: center;
            justify-content: center;
        }
        .modal-overlay.active {
            display: flex;
        }
        .modal-dialog {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 2rem;
            max-width: 400px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
        }
        .modal-title {
            font-size: 1.25rem;
            font-weight: 600;
            color: #f1f5f9;
            margin-bottom: 1rem;
        }
        .modal-message {
            font-size: 0.95rem;
            color: #cbd5e1;
            margin-bottom: 1.5rem;
            line-height: 1.5;
        }
        .modal-buttons {
            display: flex;
            gap: 0.75rem;
            justify-content: flex-end;
        }
        .modal-btn {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 0.375rem;
            font-weight: 500;
            cursor: pointer;
            font-size: 0.875rem;
            transition: all 0.2s;
        }
        .modal-btn-cancel {
            background: #334155;
            color: #e2e8f0;
        }
        .modal-btn-cancel:hover {
            background: #475569;
        }
        .modal-btn-confirm {
            background: #dc2626;
            color: #ffffff;
        }
        .modal-btn-confirm:hover {
            background: #b91c1c;
        }
    </style>
</head>
<body>
    <!-- Confirmation Modal -->
    <div id="confirmModal" class="modal-overlay">
        <div class="modal-dialog">
            <div class="modal-title" id="modalTitle">Confirm</div>
            <div class="modal-message" id="modalMessage">Are you sure?</div>
            <div class="modal-buttons">
                <button class="modal-btn modal-btn-cancel" onclick="closeConfirmModal()">Cancel</button>
                <button class="modal-btn modal-btn-confirm" id="modalConfirmBtn" onclick="confirmModalAction()">Delete</button>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="header">
            <h1>Marine Observation Editor</h1>
            <p class="subtitle">View, edit, and add detections to your marine observation data</p>
        </div>

        <div id="errorMessage" class="message error"></div>
        <div id="successMessage" class="message success"></div>

        <div class="controls-panel">
            <div class="controls-grid">
                <div class="control-group">
                    <label class="control-label">Load Data</label>
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn-primary" onclick="document.getElementById('ncInput').click()" style="flex: 1;">📁 NetCDF</button>
                        <button class="btn-secondary" onclick="document.getElementById('csvInput').click()" style="flex: 1;">📄 CSV</button>
                    </div>
                    <input type="file" id="ncInput" accept=".nc" onchange="handleNetCDFUpload(event)" style="display: none;">
                    <input type="file" id="csvInput" accept=".csv" onchange="handleCSVUpload(event)" style="display: none;">
                </div>

                <div class="control-group">
                    <label class="control-label">Filter by Species</label>
                    <input type="text" id="speciesFilter" placeholder="e.g., Chromis" onchange="applyFilters()">
                </div>

                <div class="control-group">
                    <label class="control-label">Min Confidence</label>
                    <input type="number" id="confidenceFilter" min="0" max="100" value="0" onchange="applyFilters()" placeholder="%">
                </div>

                <div class="control-group">
                    <label class="checkbox-label">
                        <input type="checkbox" id="showUnknown" checked onchange="applyFilters()">
                        Unknown Species
                    </label>
                </div>
            </div>
        </div>

        <div id="emptyState" class="empty-state">
            <p>No data loaded yet</p>
            <p class="file-format-info">Upload a NetCDF or CSV file to begin</p>
        </div>

        <div id="editorSection" style="display: none;">
            <div class="main-grid">
                <div>
                    <div class="canvas-section">
                        <div class="canvas-wrapper" id="canvasWrapper">
                            <div class="canvas-instructions" id="instructions">
                                <strong>Click and drag:</strong><br>
                                Click on one corner and drag to opposite corner
                            </div>
                            <canvas id="canvas"></canvas>
                        </div>
                    </div>

                    <div style="margin-top: 1rem; display: flex; gap: 1rem;">
                        <button class="btn-secondary" onclick="previousImage()" style="flex: 1;">← Previous</button>
                        <div style="flex: 2; text-align: center; display: flex; align-items: center; justify-content: center; gap: 0.25rem; background: rgba(30, 41, 59, 0.5); border-radius: 0.5rem; color: #60a5fa; font-weight: 600; padding: 0 0.5rem;">
                            <input type="number" id="counterInput" min="1" value="1"
                                style="width: 6rem; background: transparent; border: none; border-bottom: 1px solid #60a5fa; color: #60a5fa; font-weight: 600; font-size: 1rem; text-align: center;"
                                onchange="jumpToImage(this.value)" onkeydown="if(event.key==='Enter') jumpToImage(this.value)">
                            <span id="counterTotal"> / 0</span>
                        </div>
                        <button class="btn-secondary" onclick="nextImage()" style="flex: 1;">Next →</button>
                    </div>

                    <div style="margin-top: 1rem;">
                        <button class="btn-success" onclick="downloadModifiedNetCDF()" style="width: 100%;">💾 Save as NetCDF</button>
                        <button class="btn-danger" onclick="deleteCurrentImage()" style="width: 100%; margin-top: 0.5rem;" title="Keyboard shortcut: D">🗑️ Delete Image & Detections</button>
                    </div>
                </div>

                <div class="sidebar">
                    <div class="sidebar-panel">
                        <div class="sidebar-title">Detections</div>
                        <div class="detection-list" id="detectionList"></div>
                    </div>

                    <div class="sidebar-panel edit-panel" id="editPanel">
                        <div class="sidebar-title">Edit Detection</div>
                        <div class="form-group" style="position:relative">
                            <label>Species Name</label>
                            <input type="text" id="editSpecies" placeholder="Type name or click to pick from loaded labels…" autocomplete="off">
                            <div class="worms-dropdown" id="editSpeciesDropdown"></div>
                        </div>
                        <div class="form-group">
                            <label>Species ID (LSID)</label>
                            <input type="text" id="editSpeciesLsid" readonly placeholder="Auto-filled from WoRMS" style="color:#94a3b8;cursor:default;font-size:0.75rem;">
                        </div>
                        <div class="form-group">
                            <label>Confidence (0-1)</label>
                            <input type="number" id="editConfidence" min="0" max="1" step="0.01" placeholder="0.85">
                        </div>
                        <div class="form-group">
                            <label>Verification</label>
                            <select id="editVerification">
                                <option>PredictedByMachine</option>
                                <option>ValidatedByHuman</option>
                            </select>
                        </div>
                        <div class="button-group">
                            <button class="btn-primary btn-small" onclick="saveDetectionChanges()">Save</button>
                            <button class="btn-danger btn-small" onclick="deleteDetection()">Delete</button>
                        </div>
                    </div>

                    <div class="sidebar-panel">
                        <div class="sidebar-title">Add Detection</div>
                        <p style="font-size: 0.75rem; color: #94a3b8; margin-bottom: 1rem;">
                            Click the button below, then click and drag on the image.
                        </p>
                        <div class="form-group" style="position:relative">
                            <label>Species Name</label>
                            <input type="text" id="newSpecies" placeholder="Type name or click to pick from loaded labels…" autocomplete="off">
                            <div class="worms-dropdown" id="newSpeciesDropdown"></div>
                        </div>
                        <div class="form-group">
                            <label>Species ID (LSID)</label>
                            <input type="text" id="newSpeciesLsid" readonly placeholder="Auto-filled from WoRMS" style="color:#94a3b8;cursor:default;font-size:0.75rem;">
                        </div>
                        <div class="form-group">
                            <label>Confidence</label>
                            <input type="number" id="newConfidence" min="0" max="1" step="0.01" placeholder="0.85">
                        </div>
                        <button class="btn-primary" onclick="enableDrawingMode()" style="width: 100%; margin-top: 1rem;">
                            ✏️ Draw Detection
                        </button>
                    </div>

                    <div class="sidebar-panel">
                        <div class="sidebar-title">⌨️ Keyboard Shortcuts</div>
                        <div style="font-size: 0.75rem; color: #cbd5e1; line-height: 1.6;">
                            <div><strong>←/→</strong> Previous/Next image</div>
                            <div><strong>N</strong> Draw detection</div>
                            <div><strong>D</strong> Delete current image</div>
                            <div><strong>Enter</strong> Confirm deletion</div>
                            <div><strong>Esc</strong> Close dialog</div>
                        </div>
                    </div>

                    <div class="stats" id="stats"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let allData = [];
        let filteredData = [];
        let currentIndex = 0;
        let currentFile = null;
        let currentFileId = null;
        let drawingMode = false;
        let selectedDetectionIndex = -1;
        let useProxy = false;
        let currentImageObj = null;
        let currentImageUrl = null;
        let lastUsedSpecies = '';
        let lastUsedSpeciesLsid = '';
        let pendingDeleteCallback = null;

        function showError(msg) {
            const el = document.getElementById('errorMessage');
            el.textContent = msg;
            el.classList.add('active');
            setTimeout(() => el.classList.remove('active'), 5000);
        }

        function showSuccess(msg) {
            const el = document.getElementById('successMessage');
            el.textContent = msg;
            el.classList.add('active');
            setTimeout(() => el.classList.remove('active'), 5000);
        }

        function showConfirmModal(title, message, callback) {
            document.getElementById('modalTitle').textContent = title;
            document.getElementById('modalMessage').textContent = message;
            pendingDeleteCallback = callback;
            document.getElementById('confirmModal').classList.add('active');
            
            // Focus the confirm button and handle Enter key
            const confirmBtn = document.getElementById('modalConfirmBtn');
            setTimeout(() => confirmBtn.focus(), 100);
        }

        function closeConfirmModal() {
            document.getElementById('confirmModal').classList.remove('active');
            pendingDeleteCallback = null;
        }

        function confirmModalAction() {
            if (pendingDeleteCallback) {
                pendingDeleteCallback();
            }
            closeConfirmModal();
        }

        async function handleNetCDFUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            currentFile = file;
            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch('/api/parse-netcdf', {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error);
                }

                const result = await response.json();
                allData = result.data.map((d, i) => ({...d, id: i}));
                currentFileId = result.file_id;
                updateExistingLabelsDropdown();
                showSuccess(`✓ Loaded ${allData.length} observations`);
                currentIndex = 0;
                applyFilters();
                updateUI();
            } catch (error) {
                showError(`Error: ${error.message}`);
            }
        }

        function handleCSVUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const lines = e.target.result.split('\\n').filter(l => l.trim());
                    const urlMap = new Map();
                    const urlOrder = [];

                    lines.slice(1).forEach((line) => {
                        const v = line.split(',');
                        const url = v[2]?.trim() || '';
                        const species = v[3]?.trim() || 'unknown';
                        const sciid = v[4]?.trim() || '';
                        const confidence = parseFloat(v[5]) || 0;
                        const bbox = v[6]?.trim() || '';
                        const verif = v[7]?.trim() || 'PredictedByMachine';

                        const detection = {
                            scientificName: species,
                            scientificNameID: sciid,
                            confidence: confidence,
                            bbox: bbox,
                            identificationVerificationStatus: verif
                        };

                        if (!urlMap.has(url)) {
                            urlOrder.push(url);
                            urlMap.set(url, {
                                time: v[0]?.trim() || '',
                                depth: v[1]?.trim() || '0',
                                url: url,
                                scientificName: species,
                                confidence: confidence,
                                bbox: bbox,
                                latitude: parseFloat(v[9]) || 0,
                                longitude: parseFloat(v[10]) || 0,
                                detections: [detection],
                                modified: false
                            });
                        } else {
                            urlMap.get(url).detections.push(detection);
                        }
                    });

                    allData = urlOrder.map((url, i) => ({ ...urlMap.get(url), id: i }));
                    updateExistingLabelsDropdown();
                    showSuccess(`✓ Loaded ${allData.length} observations from CSV`);
                    currentIndex = 0;
                    applyFilters();
                    updateUI();
                } catch (error) {
                    showError('Error parsing CSV: ' + error.message);
                }
            };
            reader.readAsText(file);
        }

        function applyFilters() {
            const speciesFilter = document.getElementById('speciesFilter').value.toLowerCase();
            const confidenceFilter = parseFloat(document.getElementById('confidenceFilter').value || 0) / 100;
            const showUnknown = document.getElementById('showUnknown').checked;

            filteredData = allData.filter(item => {
                if (!showUnknown && item.scientificName.toLowerCase().includes('unknown')) return false;
                if (speciesFilter && !item.scientificName.toLowerCase().includes(speciesFilter)) return false;
                if (item.confidence < confidenceFilter) return false;
                return true;
            });

            currentIndex = 0;
            updateUI();
        }

        function updateUI() {
            const hasData = allData.length > 0;
            document.getElementById('emptyState').style.display = hasData ? 'none' : 'block';
            document.getElementById('editorSection').style.display = hasData ? 'block' : 'none';

            if (hasData) {
                redrawImage();
                updateDetectionList();
                updateCounter();
                updateStats();
            }
        }

        // Draw cached image + detections onto canvas (no network request)
        function redrawCanvas() {
            if (!currentImageObj) return;
            const canvas = document.getElementById('canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = currentImageObj.naturalWidth;
            canvas.height = currentImageObj.naturalHeight;
            ctx.drawImage(currentImageObj, 0, 0);
            drawAllDetections(ctx);
        }

        function redrawImage() {
            if (filteredData.length === 0) return;

            const current = filteredData[currentIndex];
            const canvas = document.getElementById('canvas');
            const ctx = canvas.getContext('2d');

            // Re-use cached image if URL hasn't changed
            if (currentImageUrl === current.url && currentImageObj) {
                redrawCanvas();
                return;
            }

            // Show loading state while new image fetches
            canvas.width = 600;
            canvas.height = 400;
            ctx.fillStyle = '#333';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#60a5fa';
            ctx.font = 'bold 14px sans-serif';
            ctx.fillText('Loading image...', 20, 40);

            const targetUrl = current.url;

            function showError() {
                canvas.width = 600;
                canvas.height = 400;
                ctx.fillStyle = '#1a1a1a';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.fillStyle = '#ef4444';
                ctx.font = 'bold 16px sans-serif';
                ctx.fillText('Unable to load image', 20, 60);
                ctx.font = '12px sans-serif';
                ctx.fillStyle = '#94a3b8';
                ctx.fillText('URL: ' + targetUrl.substring(0, 60) + '...', 20, 90);
                ctx.fillText('Check:', 20, 120);
                ctx.fillText('• Internet connection', 20, 145);
                ctx.fillText('• Image server availability', 20, 170);
                ctx.fillText('• Network/firewall settings', 20, 195);
            }

            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => {
                currentImageObj = img;
                currentImageUrl = targetUrl;
                redrawCanvas();
            };
            img.onerror = () => {
                console.warn('Direct load failed, trying proxy...');
                const proxyImg = new Image();
                proxyImg.crossOrigin = 'anonymous';
                proxyImg.onload = () => {
                    currentImageObj = proxyImg;
                    currentImageUrl = targetUrl;
                    redrawCanvas();
                };
                proxyImg.onerror = showError;
                proxyImg.src = '/api/proxy-image?url=' + encodeURIComponent(targetUrl);
            };
            img.src = targetUrl;
        }

        function drawAllDetections(ctx) {
            if (filteredData.length === 0) return;

            const current = filteredData[currentIndex];
            const detections = current.detections || [];

            detections.forEach((det, idx) => {
                try {
                    const bboxStr = String(det.bbox || '');
                    const bboxParts = bboxStr.split(' ').map(Number).filter(n => !isNaN(n));
                    
                    if (bboxParts.length < 4) return;
                    
                    let [x1, y1, x2, y2] = bboxParts;
                    if (x1 > x2) [x1, x2] = [x2, x1];
                    if (y1 > y2) [y1, y2] = [y2, y1];
                    
                    const isSelected = idx === selectedDetectionIndex;
                    const color = isSelected ? '#60a5fa' : '#ef4444';
                    
                    ctx.strokeStyle = color;
                    ctx.lineWidth = isSelected ? 4 : 2;
                    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

                    ctx.fillStyle = color;
                    ctx.font = 'bold 12px sans-serif';
                    const speciesName = det.scientificName || 'unknown';
                    const confidence = det.confidence || 0;
                    ctx.fillText(`${speciesName} (${(confidence * 100).toFixed(0)}%)`, x1 + 3, y1 - 3);
                } catch (e) {
                    console.warn('Error drawing detection:', det, e);
                }
            });
        }

        function updateDetectionList() {
            if (filteredData.length === 0) return;

            const current = filteredData[currentIndex];
            const detections = current.detections || [];
            const list = document.getElementById('detectionList');

            list.innerHTML = detections.map((det, idx) => `
                <div class="detection-item ${idx === selectedDetectionIndex ? 'selected' : ''}" onclick="selectDetection(${idx})">
                    <div class="detection-name">${det.scientificName}</div>
                    <div class="detection-conf">Conf: ${(det.confidence * 100).toFixed(1)}%</div>
                </div>
            `).join('');

            if (detections.length === 0) {
                list.innerHTML = '<div style="text-align: center; color: #64748b; padding: 1rem;">No detections</div>';
            }
        }

        function selectDetection(idx) {
            selectedDetectionIndex = selectedDetectionIndex === idx ? -1 : idx;
            updateUI();

            if (selectedDetectionIndex !== -1) {
                const current = filteredData[currentIndex];
                const det = current.detections[selectedDetectionIndex];
                document.getElementById('editSpecies').value = det.scientificName;
                document.getElementById('editSpeciesLsid').value = det.scientificNameID || '';
                document.getElementById('editConfidence').value = det.confidence;
                document.getElementById('editVerification').value = det.identificationVerificationStatus || 'PredictedByMachine';
                document.getElementById('editPanel').classList.add('active');
            } else {
                document.getElementById('editPanel').classList.remove('active');
            }
        }

        async function saveDetectionChanges() {
            if (currentIndex < 0 || selectedDetectionIndex < 0) return;

            const current = filteredData[currentIndex];
            const det = current.detections[selectedDetectionIndex];

            det.scientificName = document.getElementById('editSpecies').value || 'unknown';
            let lsid = document.getElementById('editSpeciesLsid').value || '';
            if (!lsid && det.scientificName !== 'unknown') {
                try {
                    const r = await fetch('/api/worms-lsid?name=' + encodeURIComponent(det.scientificName));
                    lsid = (await r.json()).lsid || '';
                    document.getElementById('editSpeciesLsid').value = lsid;
                } catch (_) {}
            }
            det.scientificNameID = lsid;
            det.confidence = parseFloat(document.getElementById('editConfidence').value) || 0.5;
            det.identificationVerificationStatus = document.getElementById('editVerification').value;

            redrawImage();
            updateDetectionList();
            showSuccess('✓ Detection updated');
        }

        function deleteDetection() {
            if (currentIndex < 0 || selectedDetectionIndex < 0) return;

            const current = filteredData[currentIndex];
            current.detections.splice(selectedDetectionIndex, 1);
            selectedDetectionIndex = -1;

            redrawImage();
            updateDetectionList();
            showSuccess('✓ Detection deleted');
        }

        function deleteCurrentImage() {
            if (currentIndex < 0) return;

            const current = filteredData[currentIndex];
            const imageUrl = current.url;
            
            // Show confirmation modal with callback
            showConfirmModal(
                '🗑️ Delete Image?',
                `Are you sure you want to delete this image and all its ${(current.detections || []).length} detections/bounding boxes? This cannot be undone.`,
                () => {
                    // This is the callback that runs if user confirms
                    // Mark as modified
                    current.modified = true;
                    
                    // Remove the image from allData
                    const allIndex = allData.findIndex(d => d.url === imageUrl);
                    if (allIndex >= 0) {
                        allData.splice(allIndex, 1);
                    }

                    // Remove from filteredData
                    filteredData.splice(currentIndex, 1);

                    // Update currentIndex
                    if (filteredData.length === 0) {
                        currentIndex = -1;
                        selectedDetectionIndex = -1;
                        currentImageObj = null;
                        currentImageUrl = null;
                    } else if (currentIndex >= filteredData.length) {
                        // If we deleted the last image, go to the new last image
                        currentIndex = filteredData.length - 1;
                        selectedDetectionIndex = -1;
                        currentImageObj = null;
                        currentImageUrl = null;
                    } else {
                        // Keep currentIndex the same — the next image has shifted into this position
                        selectedDetectionIndex = -1;
                        currentImageObj = null;
                        currentImageUrl = null;
                    }

                    updateUI();
                    updateStats();
                    showSuccess(`✓ Image deleted (${filteredData.length} remaining)`);
                }
            );
        }

        function enableDrawingMode() {
            drawingMode = true;
            const instructionsEl = document.getElementById('instructions');
            instructionsEl.classList.add('active');
            
            // Auto-hide instructions after 1 second to avoid blocking the image
            setTimeout(() => {
                if (drawingMode) {
                    instructionsEl.classList.remove('active');
                }
            }, 1000);

            const canvas = document.getElementById('canvas');
            canvas.style.cursor = 'crosshair';

            let startX = 0, startY = 0, drawing = false;

            // Scale CSS pixel position to canvas/image pixel coordinates
            function toImageCoords(e) {
                const rect = canvas.getBoundingClientRect();
                const scaleX = canvas.width / rect.width;
                const scaleY = canvas.height / rect.height;
                return {
                    x: (e.clientX - rect.left) * scaleX,
                    y: (e.clientY - rect.top) * scaleY
                };
            }

            canvas.onmousedown = (e) => {
                if (!drawingMode) return;
                const pos = toImageCoords(e);
                startX = pos.x;
                startY = pos.y;
                drawing = true;
            };

            canvas.onmousemove = (e) => {
                if (!drawingMode || !drawing) return;
                // Redraw from cache — no image reload, no flicker
                redrawCanvas();

                const pos = toImageCoords(e);
                const ctx = canvas.getContext('2d');
                ctx.strokeStyle = '#60a5fa';
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 5]);
                ctx.strokeRect(
                    Math.min(startX, pos.x),
                    Math.min(startY, pos.y),
                    Math.abs(pos.x - startX),
                    Math.abs(pos.y - startY)
                );
                ctx.setLineDash([]);
            };

            canvas.onmouseup = (e) => {
                if (!drawingMode || !drawing) return;
                const pos = toImageCoords(e);
                if (Math.abs(pos.x - startX) > 5 && Math.abs(pos.y - startY) > 5) {
                    finishDrawing(startX, startY, pos.x, pos.y);
                }
                drawing = false;
            };

            canvas.onmouseleave = () => {
                drawing = false;
                redrawCanvas();
            };
        }

        async function finishDrawing(x1, y1, x2, y2) {
            if (currentIndex < 0) return;

            const minX = Math.min(x1, x2);
            const minY = Math.min(y1, y2);
            const maxX = Math.max(x1, x2);
            const maxY = Math.max(y1, y2);

            const current = filteredData[currentIndex];
            if (!current.detections) current.detections = [];

            const species = document.getElementById('newSpecies').value || 'new detection';
            let lsid = document.getElementById('newSpeciesLsid').value || '';
            if (!lsid && species !== 'new detection') {
                try {
                    const r = await fetch('/api/worms-lsid?name=' + encodeURIComponent(species));
                    lsid = (await r.json()).lsid || '';
                    document.getElementById('newSpeciesLsid').value = lsid;
                } catch (_) {}
            }
            const confidence = parseFloat(document.getElementById('newConfidence').value) || 0.5;

            current.detections.push({
                scientificName: species,
                scientificNameID: lsid,
                confidence: confidence,
                bbox: `${minX} ${minY} ${maxX} ${maxY}`,
                identificationVerificationStatus: 'PredictedByMachine'
            });

            drawingMode = false;
            document.getElementById('instructions').classList.remove('active');
            document.getElementById('canvas').style.cursor = 'default';

            lastUsedSpecies = species;
            lastUsedSpeciesLsid = lsid;
            document.getElementById('newConfidence').value = '';
            updateExistingLabelsDropdown();
            const lsel = document.getElementById('existingLabelSelect');
            if (lsel) lsel.value = species;

            redrawImage();
            updateDetectionList();
            showSuccess('✓ Detection added');
        }

        function resetDrawingMode() {
            drawingMode = false;
            document.getElementById('instructions').classList.remove('active');
            const canvas = document.getElementById('canvas');
            canvas.style.cursor = 'default';
            canvas.onmousedown = null;
            canvas.onmousemove = null;
            canvas.onmouseup = null;
            canvas.onmouseleave = null;
        }

        document.addEventListener('keydown', (e) => {
            const modal = document.getElementById('confirmModal');
            const isModalOpen = modal.classList.contains('active');
            
            // Handle modal keyboard shortcuts
            if (isModalOpen) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    confirmModalAction();
                    return;
                }
                if (e.key === 'Escape') {
                    closeConfirmModal();
                    return;
                }
            }
            
            // Close modal with Escape key
            if (e.key === 'Escape') {
                if (modal.classList.contains('active')) {
                    closeConfirmModal();
                    return;
                }
            }
            
            // Don't trigger navigation when typing in an input/select
            if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
            if (e.key === 'ArrowLeft') previousImage();
            else if (e.key === 'ArrowRight') nextImage();
            else if (e.key === 'd' || e.key === 'D') deleteCurrentImage();
            else if (e.key === 'n' || e.key === 'N') enableDrawingMode();
        });

        // Close modal when clicking outside the dialog
        document.getElementById('confirmModal').addEventListener('click', (e) => {
            if (e.target.id === 'confirmModal') {
                closeConfirmModal();
            }
        });

        // WoRMS autocomplete
        // opts = { getLocalLabels: () => [[name, lsid], ...], autoFetchOnBlur: bool }
        function setupWormsAutocomplete(inputId, lsidId, dropdownId, opts) {
            const input = document.getElementById(inputId);
            const lsidInput = document.getElementById(lsidId);
            const dropdown = document.getElementById(dropdownId);
            let debounceTimer;

            input.addEventListener('input', () => {
                // Clear LSID when the user edits the name manually
                lsidInput.value = '';
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(async () => {
                    const q = input.value.trim();
                    dropdown.innerHTML = '';
                    if (q.length < 1) return;
                    let hasItems = false;

                    // Show matching labels from the loaded dataset first
                    if (opts && opts.getLocalLabels) {
                        const locals = opts.getLocalLabels()
                            .filter(([name]) => name.toLowerCase().includes(q.toLowerCase()));
                        if (locals.length > 0) {
                            const hdr = document.createElement('div');
                            hdr.className = 'worms-section-header';
                            hdr.textContent = 'From dataset';
                            dropdown.appendChild(hdr);
                            locals.slice(0, 6).forEach(([name, lsid]) => {
                                const item = document.createElement('div');
                                item.className = 'worms-item';
                                item.innerHTML = name + '<span class="worms-badge" style="background:#1a3a1a;color:#86efac">dataset</span>';
                                item.addEventListener('mousedown', (e) => {
                                    e.preventDefault();
                                    input.value = name;
                                    lsidInput.value = lsid || '';
                                    dropdown.innerHTML = '';
                                });
                                dropdown.appendChild(item);
                            });
                            hasItems = true;
                        }
                    }

                    // WoRMS suggestions for queries >= 3 chars
                    if (q.length >= 3) {
                        try {
                            const res = await fetch('/api/worms-suggest?q=' + encodeURIComponent(q));
                            const suggestions = await res.json();
                            if (suggestions.length > 0) {
                                if (hasItems) {
                                    const hdr = document.createElement('div');
                                    hdr.className = 'worms-section-header';
                                    hdr.textContent = 'From WoRMS';
                                    dropdown.appendChild(hdr);
                                }
                                suggestions.forEach(s => {
                                    const item = document.createElement('div');
                                    item.className = 'worms-item';
                                    const isSynonym = s.status && s.status !== 'accepted';
                                    const badge = `<span class="worms-badge${isSynonym ? ' synonym' : ''}">${s.status || 'unknown'}</span>`;
                                    const synonym = (isSynonym && s.valid_name)
                                        ? ` <span class="worms-synonym">→ ${s.valid_name}</span>` : '';
                                    item.innerHTML = s.name + badge + synonym;
                                    item.addEventListener('mousedown', (e) => {
                                        e.preventDefault(); // keep focus on input
                                        // Use the accepted name if this is a synonym
                                        input.value = (isSynonym && s.valid_name) ? s.valid_name : s.name;
                                        lsidInput.value = s.lsid || '';
                                        dropdown.innerHTML = '';
                                    });
                                    dropdown.appendChild(item);
                                });
                            }
                        } catch (_) {}
                    }
                }, 300);
            });

            input.addEventListener('blur', async () => {
                // Delay so mousedown on item fires first
                setTimeout(() => { dropdown.innerHTML = ''; }, 150);
                // Auto-fetch LSID on blur when field is empty after manual typing
                if (opts && opts.autoFetchOnBlur) {
                    const name = input.value.trim();
                    if (name && !lsidInput.value) {
                        try {
                            const r = await fetch('/api/worms-lsid?name=' + encodeURIComponent(name));
                            lsidInput.value = (await r.json()).lsid || '';
                        } catch (_) {}
                    }
                }
            });

            // Show all loaded labels immediately when input is focused and empty
            if (opts && opts.showAllOnFocus && opts.getLocalLabels) {
                input.addEventListener('focus', () => {
                    if (input.value.trim().length > 0) return; // let 'input' event handle it
                    const all = opts.getLocalLabels();
                    if (all.length === 0) return;
                    dropdown.innerHTML = '';
                    const hdr = document.createElement('div');
                    hdr.className = 'worms-section-header';
                    hdr.textContent = 'From dataset';
                    dropdown.appendChild(hdr);
                    all.forEach(([name, lsid]) => {
                        const item = document.createElement('div');
                        item.className = 'worms-item';
                        item.innerHTML = name + '<span class="worms-badge" style="background:#1a3a1a;color:#86efac">dataset</span>';
                        item.addEventListener('mousedown', (e) => {
                            e.preventDefault();
                            input.value = name;
                            lsidInput.value = lsid || '';
                            dropdown.innerHTML = '';
                        });
                        dropdown.appendChild(item);
                    });
                });
            }
        }

        function updateExistingLabelsDropdown() {
            const select = document.getElementById('existingLabelSelect');
            if (!select) return;
            const labelMap = new Map();
            allData.forEach(record => {
                (record.detections || []).forEach(det => {
                    if (det.scientificName && !labelMap.has(det.scientificName)) {
                        labelMap.set(det.scientificName, det.scientificNameID || '');
                    }
                });
            });
            const sorted = [...labelMap.entries()].sort((a, b) => a[0].localeCompare(b[0]));
            const prev = select.value;
            select.innerHTML = '<option value="">— pick from loaded labels —</option>';
            sorted.forEach(([name, lsid]) => {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                opt.dataset.lsid = lsid;
                select.appendChild(opt);
            });
            if (prev) select.value = prev;
        }

        function selectExistingLabel(name) {
            if (!name) return;
            const select = document.getElementById('existingLabelSelect');
            let lsid = '';
            for (const opt of select.options) {
                if (opt.value === name) { lsid = opt.dataset.lsid || ''; break; }
            }
            document.getElementById('newSpecies').value = name;
            document.getElementById('newSpeciesLsid').value = lsid;
        }

        setupWormsAutocomplete('editSpecies', 'editSpeciesLsid', 'editSpeciesDropdown', {
            autoFetchOnBlur: true,
            showAllOnFocus: true,
            getLocalLabels: () => {
                const labelMap = new Map();
                allData.forEach(record => {
                    (record.detections || []).forEach(det => {
                        if (det.scientificName && !labelMap.has(det.scientificName)) {
                            labelMap.set(det.scientificName, det.scientificNameID || '');
                        }
                    });
                });
                return [...labelMap.entries()].sort((a, b) => a[0].localeCompare(b[0]));
            }
        });
        setupWormsAutocomplete('newSpecies', 'newSpeciesLsid', 'newSpeciesDropdown', {
            showAllOnFocus: true,
            getLocalLabels: () => {
                const labelMap = new Map();
                allData.forEach(record => {
                    (record.detections || []).forEach(det => {
                        if (det.scientificName && !labelMap.has(det.scientificName)) {
                            labelMap.set(det.scientificName, det.scientificNameID || '');
                        }
                    });
                });
                return [...labelMap.entries()].sort((a, b) => a[0].localeCompare(b[0]));
            }
        });

        function previousImage() {
            if (currentIndex > 0) {
                currentIndex--;
                selectedDetectionIndex = -1;
                currentImageObj = null;
                currentImageUrl = null;
                resetDrawingMode();
                updateUI();
            }
        }

        function nextImage() {
            if (currentIndex < filteredData.length - 1) {
                currentIndex++;
                selectedDetectionIndex = -1;
                currentImageObj = null;
                currentImageUrl = null;
                resetDrawingMode();
                updateUI();
            }
        }

        function updateCounter() {
            document.getElementById('counterInput').value = currentIndex + 1;
            document.getElementById('counterTotal').textContent = ` / ${filteredData.length}`;
        }

        function jumpToImage(val) {
            const n = parseInt(val);
            if (isNaN(n)) { document.getElementById('counterInput').value = currentIndex + 1; return; }
            const idx = Math.max(0, Math.min(filteredData.length - 1, n - 1));
            currentIndex = idx;
            selectedDetectionIndex = -1;
            currentImageObj = null;
            currentImageUrl = null;
            resetDrawingMode();
            updateUI();
        }

        function updateStats() {
            const totalDets = allData.reduce((sum, d) => sum + (d.detections?.length || 0), 0);
            document.getElementById('stats').innerHTML = `
                <p><strong>Total Records:</strong> ${filteredData.length}</p>
                <p><strong>Total Detections:</strong> ${totalDets}</p>
                <p><strong>Modified:</strong> ${allData.filter(d => d.modified).length}</p>
            `;
        }

        async function downloadModifiedNetCDF() {
            if (!currentFileId) {
                showError('No NetCDF file loaded');
                return;
            }

            try {
                const response = await fetch('/api/save-netcdf', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({file_id: currentFileId, data: allData})
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error);
                }

                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${currentFile.name.replace('.nc', '_modified.nc')}`;
                a.click();
                window.URL.revokeObjectURL(url);

                showSuccess('✓ NetCDF file saved');
            } catch (error) {
                showError(`Error: ${error.message}`);
            }
        }
    </script>
</body>
</html>'''

# Helper functions (same as before)
def decode_clean(byte_array):
    if isinstance(byte_array, bytes):
        return byte_array.decode('utf-8').replace('\x00', '').strip()
    return str(byte_array).replace('\x00', '').strip()

def parse_bbox_fixed_width(bbox_raw):
    # Decode bytes first to avoid the str(b'...') wrapper mangling the value
    if isinstance(bbox_raw, (bytes, bytearray)):
        raw_str = bbox_raw.decode('utf-8', errors='replace').replace('\x00', ' ').strip()
    else:
        raw_str = str(bbox_raw).replace('\x00', ' ').strip()
    numbers = [p.strip() for p in raw_str.split() if p.strip()]
    if len(numbers) >= 4:
        return ' '.join(numbers[:4])
    return ' '.join(numbers)

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/parse-netcdf', methods=['POST'])
def parse_netcdf():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if not file.filename.endswith('.nc'):
            return jsonify({'error': 'File must be .nc'}), 400
        
        file_content = file.read()
        
        with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        
        try:
            ds = xr.open_dataset(tmp_path)
            n_obs = ds.sizes.get('obs', 0)

            # Guard against malformed files where a parallel coord is shorter
            # than obs (can happen with old saves before the parallel-coord fix).
            n_obs_safe = n_obs
            for _name in ('time', 'depth', 'latitude', 'longitude'):
                if _name in ds.coords:
                    n_obs_safe = min(n_obs_safe, ds[_name].shape[0])
                elif _name in ds.data_vars:
                    n_obs_safe = min(n_obs_safe, ds[_name].shape[0])
            n_obs = n_obs_safe

            times = ds['time'].values
            # Use an ordered dict so rows with the same URL are merged into
            # one observation with multiple detections rather than appearing
            # as duplicate image slots.
            url_to_record = {}
            url_order = []

            for i in range(n_obs):
                try:
                    if hasattr(times[i], 'astype'):
                        unix_timestamp = times[i].astype('datetime64[s]').astype(int)
                    else:
                        unix_timestamp = float(times[i])
                    iso_time = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).isoformat().replace('+00:00', 'Z')
                except:
                    iso_time = str(times[i])

                url = decode_clean(ds['associatedMedia'].values[i])
                species = decode_clean(ds['scientificName'].values[i])
                species_id = decode_clean(ds['scientificNameID'].values[i]) if 'scientificNameID' in ds else ''
                bbox_raw = ds['bbox'].values[i]
                bbox_str = parse_bbox_fixed_width(bbox_raw)

                detection = {
                    'scientificName': species,
                    'scientificNameID': species_id,
                    'confidence': float(ds['confidence'].values[i]),
                    'bbox': bbox_str,
                    'identificationVerificationStatus': decode_clean(ds['identificationVerificationStatus'].values[i])
                }

                if url not in url_to_record:
                    url_order.append(url)
                    url_to_record[url] = {
                        'time': iso_time,
                        'depth': int(ds['depth'].values[i]),
                        'url': url,
                        'scientificName': species,
                        'confidence': float(ds['confidence'].values[i]),
                        'bbox': bbox_str,
                        'identificationVerificationStatus': decode_clean(ds['identificationVerificationStatus'].values[i]),
                        'latitude': float(ds['latitude'].values[i]),
                        'longitude': float(ds['longitude'].values[i]),
                        'detections': [detection],
                        'modified': False
                    }
                else:
                    url_to_record[url]['detections'].append(detection)

            data = [url_to_record[u] for u in url_order]
            
            ds.close()
            # Keep the temp file for save; return its ID to the client
            file_id = os.path.basename(tmp_path)
            _netcdf_cache[file_id] = tmp_path

            return jsonify({'data': data, 'file_id': file_id})
            
        except Exception as e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise e
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/save-netcdf', methods=['POST'])
def save_netcdf():
    try:
        payload  = request.get_json(force=True, silent=False)
        file_id  = payload.get('file_id', '')
        data     = payload.get('data', [])

        tmp_path = _netcdf_cache.get(file_id)
        if not tmp_path or not os.path.exists(tmp_path):
            return jsonify({'error': 'Original file not found on server. Please re-upload the NetCDF file.'}), 400

        try:
            ds = xr.open_dataset(tmp_path)
            n_obs = ds.sizes.get('obs', 0)

            # Map each URL to its first occurrence row in the source dataset.
            # We must look up by URL rather than using enumerate position,
            # because after the first save a URL's first row in the dataset
            # is no longer at position i (multiple detections per URL shift
            # all subsequent rows forward).
            url_to_first_row = {}
            for k in range(n_obs):
                url_k = decode_clean(ds['associatedMedia'].values[k])
                if url_k not in url_to_first_row:
                    url_to_first_row[url_k] = k

            # Build a flat plan: one (source_index, detection_dict) per output row.
            # Each user-added detection becomes its own new row, inheriting the
            # parent observation's metadata (time, depth, url, lat/lon, etc.).
            obs_plan = []
            for record in data:
                url = record.get('url', '')
                first_row = url_to_first_row.get(url)
                if first_row is None:
                    continue  # URL not in dataset — skip
                dets = record.get('detections') or []
                if dets:
                    for det in dets:
                        obs_plan.append((first_row, det))
                else:
                    obs_plan.append((first_row, None))

            n_new = len(obs_plan)
            src_idx = [i for i, _ in obs_plan]

            # Reindex every obs-dimensioned variable to the new row list
            new_vars = {}
            for var in ds.data_vars:
                arr = ds[var].values
                if 'obs' in ds[var].dims:
                    new_vars[var] = xr.DataArray(
                        arr[src_idx], dims=ds[var].dims, attrs=ds[var].attrs)
                else:
                    new_vars[var] = ds[var].copy()

            # Override detection-specific fields for every row
            sci    = new_vars['scientificName'].values.copy()
            conf   = new_vars['confidence'].values.copy()
            bbox   = new_vars['bbox'].values.copy()
            verif  = new_vars['identificationVerificationStatus'].values.copy()
            # scientificNameID may not exist in older files — create if absent
            if 'scientificNameID' in new_vars:
                sciid = new_vars['scientificNameID'].values.copy()
                sciid_attrs = ds['scientificNameID'].attrs
                sciid_dims  = ds['scientificNameID'].dims
            else:
                sciid = np.array([b''] * n_new, dtype=object)
                sciid_attrs = {}
                sciid_dims  = ds['scientificName'].dims

            for j, (_, det) in enumerate(obs_plan):
                if det is None:
                    continue
                sci[j]    = det['scientificName'].encode('utf-8')
                sciid[j]  = (det.get('scientificNameID') or '').encode('utf-8')
                conf[j]   = float(det['confidence'])
                bv        = det.get('bbox', '')
                bbox[j]   = bv.encode('utf-8') if isinstance(bv, str) else bv
                verif[j]  = det.get(
                    'identificationVerificationStatus', 'PredictedByMachine'
                ).encode('utf-8')

            new_vars['scientificName'] = xr.DataArray(
                sci,   dims=ds['scientificName'].dims,   attrs=ds['scientificName'].attrs)
            new_vars['scientificNameID'] = xr.DataArray(
                sciid, dims=sciid_dims, attrs=sciid_attrs)
            new_vars['confidence'] = xr.DataArray(
                conf,  dims=ds['confidence'].dims,       attrs=ds['confidence'].attrs)
            new_vars['bbox'] = xr.DataArray(
                bbox,  dims=ds['bbox'].dims,             attrs=ds['bbox'].attrs)
            new_vars['identificationVerificationStatus'] = xr.DataArray(
                verif, dims=ds['identificationVerificationStatus'].dims,
                attrs=ds['identificationVerificationStatus'].attrs)

            # Rebuild coordinates, renumbering the obs dimension if present.
            # Also reindex any 1-D coordinate that is parallel to obs
            # (same original length, own dimension name, e.g. 'time', 'depth',
            # 'latitude', 'longitude', 'platform_id', 'sensor_id').
            new_coords = {}
            for coord in ds.coords:
                c_dims = ds[coord].dims
                c_arr  = ds[coord].values
                if 'obs' in c_dims:
                    if coord == 'obs':
                        new_coords[coord] = np.arange(n_new)
                    else:
                        new_coords[coord] = (c_dims, c_arr[src_idx])
                elif c_arr.ndim == 1 and c_arr.shape[0] == n_obs:
                    # Parallel coordinate — reindex to match new obs length
                    new_coords[coord] = (c_dims[0], c_arr[src_idx])
                else:
                    new_coords[coord] = ds[coord]

            ds_out = xr.Dataset(new_vars, coords=new_coords, attrs=ds.attrs)

            output_path = tempfile.mktemp(suffix='.nc')
            ds_out.to_netcdf(output_path)
            ds.close()
            # Do NOT delete tmp_path — keep it cached for future saves

            with open(output_path, 'rb') as f:
                file_data = f.read()

            os.unlink(output_path)
            
            return send_file(
                io.BytesIO(file_data),
                mimetype='application/octet-stream',
                as_attachment=True,
                download_name='modified.nc'
            )
            
        except Exception as e:
            traceback.print_exc()
            raise e
            
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': traceback.format_exc()}), 500

if __name__ == '__main__':
    print("=" * 60)
    print("Marine Observation Editor v2 - With Image Proxy")
    print("=" * 60)
    print("\n✓ Starting server...")
    print("\n📱 Open your browser and go to:")
    print("\n   http://localhost:5000")
    print("\nFeatures:")
    print("  • View detections from NetCDF")
    print("  • Edit existing detections")
    print("  • Add new detections by drawing")
    print("  • Image proxy for CORS support")
    print("\n Press Ctrl+C to stop the server")
    print("\n" + "=" * 60)
    
    app.run(debug=True, port=5000)
