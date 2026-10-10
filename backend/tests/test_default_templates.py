import json
import os

def test_designer_templates_exist_and_metadata():
    path = os.path.join(os.path.dirname(__file__), "..", "app", "seeds", "default_templates.json")
    with open(path) as f:
        templates = json.load(f)
    
    # We expect 3 "Designer" templates
    designer_templates = [t for t in templates if t["name"].startswith("Designer ")]
    assert len(designer_templates) == 3
    
    expected = {
        "Designer — Quote": "quote",
        "Designer — News": "news",
        "Designer — Update": "news",
    }
    
    for tmpl in designer_templates:
        name = tmpl["name"]
        assert name in expected
        assert tmpl["category"] == expected[name]
        
        objects = tmpl["template_json"]["objects"]
        image_slot = next((obj for obj in objects if obj.get("placeholderRole") == "image"), None)
        assert image_slot is not None
        assert image_slot["width"] * image_slot.get("scaleX", 1) == 1080
        assert image_slot["height"] * image_slot.get("scaleY", 1) == 1350
        assert image_slot["left"] == 0
        assert image_slot["top"] == 0

