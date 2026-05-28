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
pip install flask xarray numpy --break-system-packages
```

### Start the Server
```bash
python3 web_server_advanced.py
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

---

## How to Use

### 1. **Load Your Data**

Click either:
- **📁 NetCDF** - Upload your .nc file directly
- **📄 CSV** - Upload a CSV file

The system will parse and display all observations.

### 2. **Navigate Images**

Use the **Previous/Next** buttons or the counter to browse through observations.

### 3. **Edit Existing Detections**

1. Click on a detection in the **Detections** panel (left sidebar)
2. The detection will be highlighted in blue on the image
3. The **Edit Detection** panel appears with:
   - **Species Name** - Change the scientific name
   - **Confidence** - Adjust confidence score (0-1)
   - **Verification** - Mark as validated or machine-predicted
4. Click **Save** to apply changes

### 4. **Add New Detections**

There are two ways:

#### Method A: Draw on Image
1. In the **Add Detection** panel, enter:
   - Species name (e.g., "Chromis chromis")
   - Confidence (0-1, e.g., 0.85)
2. Click **✏️ Draw Detection**
3. Click 2 points on the image to create a bounding box:
   - First click = top-left corner
   - Second click = bottom-right corner
4. The detection is added automatically

#### Method B: Manual Entry (Future Feature)
You can also add detections manually through the API.

### 5. **Delete Detections**

1. Select a detection in the panel
2. Click **Delete** in the Edit Detection panel
3. The detection is removed immediately

### 6. **Save Your Changes**

Click **💾 Save as NetCDF** to:
- Create a modified copy of your NetCDF file
- All edits, additions, and deletions are preserved
- File is downloaded as `your_file_modified.nc`

---

## Features Explained

### Filtering Panel
- **Filter by Species** - Search for specific species
- **Min Confidence** - Only show detections above a confidence threshold
- **Unknown Species** - Toggle to show/hide unidentified detections

### Sidebar Panels

#### Detections Panel
Shows all detections for the current image:
- Click to select a detection
- Selected detections appear in blue
- Shows species name and confidence

#### Edit Detection Panel
Appears when you select a detection:
- Edit the three key fields (species, confidence, verification)
- **Save** - Apply changes
- **Delete** - Remove detection

#### Add Detection Panel
For creating new detections:
- Enter species name
- Set confidence level
- Click **Draw Detection** to activate drawing mode
- Click 2 points on image to create bbox

#### Statistics Panel
Shows:
- Total records in dataset
- Total detections across all images
- Number of images with modifications

---

## Data Formats

### NetCDF Format
Your modified file will contain:
```
Dimensions: obs (number of observations)

Variables:
- time: timestamps
- depth: water depth
- associatedMedia: image URLs
- scientificName: species names (updated)
- confidence: confidence scores (updated)
- bbox: bounding boxes (updated)
- latitude, longitude: location coordinates
```

### CSV Format
If you load from CSV, you can still:
- Edit detections
- Add new detections
- BUT: You cannot save back to NetCDF
  (You'd need to load a NetCDF file first)

---

## Workflow Examples

### Example 1: Fix Misidentified Species

1. Load your NetCDF file
2. Browse to an image with wrong species
3. Click the detection to select it
4. Change the species name in the Edit panel
5. Click Save
6. Continue to next image
7. When done, click "Save as NetCDF"
8. Download the corrected file

### Example 2: Add Missing Detections

1. Load your NetCDF file
2. Find an image missing a detection
3. In "Add Detection" panel, enter:
   - Species: "Chromis chromis"
   - Confidence: "0.80"
4. Click "Draw Detection"
5. Click 2 points on the fish to create the bbox
6. Detection is added
7. Repeat for other images
8. Save and download

### Example 3: Quality Control Cleanup

1. Load your dataset
2. Filter by low confidence (e.g., < 30%)
3. Review each detection
4. Delete false positives
5. Save corrected dataset

---

## Technical Details

### Data Structure (Internal)

Each observation has:
```javascript
{
  id: 0,
  time: "2025-07-13T10:58:00Z",
  depth: 20,
  url: "https://...",
  latitude: 41.18,
  longitude: 1.75,
  detections: [
    {
      scientificName: "Chromis chromis",
      confidence: 0.884,
      bbox: "1574.853 431.967 1649.016 501.325",
      identificationVerificationStatus: "PredictedByMachine"
    }
  ],
  modified: false
}
```

### Bounding Box Format
Format: `x1 y1 x2 y2` (pixel coordinates)
- `x1, y1` = top-left corner
- `x2, y2` = bottom-right corner

When you draw on the image, coordinates are automatically calculated.

---

## Common Tasks

### How do I undo changes?
Reload the original file without saving.

### Can I edit multiple images at once?
Currently, one image at a time. You can:
1. Make changes to each image
2. Save all at once when finished
3. Download the modified file

### What if images won't load?
The images are hosted on `ancona-test.obsea.es`. Check:
- Internet connection
- Server is accessible from your network
- Browser allows mixed content (if using HTTP)

### Can I add comments or notes?
The current version doesn't support comments, but you can:
- Use the species name field to add notes
- Export data and add notes in Excel/Python

### How do I batch delete by species?
1. Filter by species name
2. Select each detection and delete
3. Save when done

---

## API Endpoints (For Developers)

### Parse NetCDF
```
POST /api/parse-netcdf
Content-Type: multipart/form-data

file: [binary NetCDF file]

Response:
{
  "data": [
    {
      "id": 0,
      "time": "2025-07-13T...",
      "url": "https://...",
      "detections": [...]
    }
  ]
}
```

### Save Modified NetCDF
```
POST /api/save-netcdf
Content-Type: multipart/form-data

file: [binary NetCDF file]
data: JSON array of modified records

Response:
[binary NetCDF file]
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Module not found" | Run `pip install flask xarray numpy --break-system-packages` |
| Images won't load | Check internet connection, image server may be down |
| Drawing not working | Make sure you clicked "Draw Detection" first |
| Can't save NetCDF | Make sure you loaded a NetCDF file (not CSV) |
| File download doesn't start | Check browser download settings |

---

## Advanced Usage

### Batch Processing (Python)

After downloading your modified NetCDF:

```python
import xarray as xr

# Load your modified file
ds = xr.open_dataset('your_file_modified.nc')

# Do further processing
print(ds)

# Export to other formats
ds.to_pandas().to_csv('output.csv')
```

### Merge Multiple Edits

If you edit the same file multiple times:

```python
import xarray as xr
import numpy as np

# Load original
original = xr.open_dataset('original.nc')

# Load modified version 1
v1 = xr.open_dataset('modified_v1.nc')

# Combine changes (manual merge)
# Implementation depends on your use case
```

---

## Performance Notes

- **Supports:** Hundreds to thousands of observations
- **Image Loading:** ~1-2 seconds per image (depends on internet)
- **Drawing:** Instant feedback
- **Save/Download:** 2-5 seconds depending on file size

---

## Future Features (Planned)

- 🔄 Undo/Redo support
- 📝 Add comments and notes to detections
- 🎨 More drawing tools (polygons, free-draw)
- 📊 Batch operations
- 🔗 Integration with other annotation tools
- 📱 Mobile-optimized interface

---

## Support & Feedback

If you encounter issues or have suggestions:
1. Check the troubleshooting section
2. Verify all dependencies are installed
3. Ensure your NetCDF file has the expected structure

---

## Next Steps

1. Start the server: `python3 web_server_advanced.py`
2. Open http://localhost:5000
3. Load your NetCDF file
4. Begin editing!
5. Save and download when complete

Happy editing! 🐠📊
