import re
import pytest

from app.services import video_templates


DENYLIST = [
    "Ferrari",
    "Red Bull",
    "Mercedes",
    "McLaren",
    "Monaco",
    "Spa",
    "Monza",
    "Silverstone",
    "Petronas",
    "Shell",
    "Brabham",
    "Tyrrell",
    "Lotus",
]


def test_new_templates_load_and_list():
    keys = [t.key for t in video_templates.list_templates()]
    assert "motorsport_legend_innovation" in keys
    assert "motorsport_corner_masterclass" in keys

    t_innov = video_templates.get_template("motorsport_legend_innovation")
    assert t_innov.key == "motorsport_legend_innovation"
    assert t_innov.model == "omni-flash"
    assert t_innov.aspect == "9:16"
    assert t_innov.clip_seconds == 10
    assert t_innov.credits_per_clip == 15
    assert t_innov.clip_options == (2,)
    assert t_innov.default_clips == 2

    t_corner = video_templates.get_template("motorsport_corner_masterclass")
    assert t_corner.key == "motorsport_corner_masterclass"
    assert t_corner.model == "omni-flash"
    assert t_corner.aspect == "9:16"
    assert t_corner.clip_seconds == 10
    assert t_corner.credits_per_clip == 15
    assert t_corner.clip_options == (2,)
    assert t_corner.default_clips == 2

    # Verify underscore files are not listed
    raw_keys = [t.key for t in video_templates.list_templates()]
    assert not any(k.startswith("_") for k in raw_keys)


EXPECTED_EXAMPLE_PROMPTS = {
    "motorsport_tech_explainer": [
        'Photorealistic 3D CGI, premium motorsport engineering documentary style, one continuous camera move with no cuts. A modern open-wheel single-seater Grand Prix race car in a deep glossy scarlet red with white and black accents livery with the name "FORMULA ARCHIVES" in large clean white block capital letters on both sidepods, no other text, no sponsor logos, no numbers, no F1 logo, no Formula 1 logo or wordmark, no FIA logo, no watermark in any corner, speeds along a sunny circuit seen from a high chase angle. The camera flies forward and down into an extreme close-up of the rear wing. The rear wing bodywork turns semi-transparent like an X-ray cutaway, explicitly revealing the DRS mechanism inside the central pillar: a slim hydraulic actuator cylinder with glowing orange and blue hydraulic fluid, a piston sliding forward, small gears and braided steel hoses. The piston pushes and the upper flap of the rear wing visibly rotates upward, opening a wide slot between the two wing elements. Crisp mechanical macro detail, motion blur on the asphalt below. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: a confident British male documentary narrator with a deep, warm, clear voice, speaking at a steady pace, says in English: "This is DRS, the Drag Reduction System. Inside the rear wing, a small hydraulic actuator pushes a piston to tilt the upper flap open."',
        'Continue seamlessly from this exact frame, same photorealistic 3D CGI engineering documentary style, same scarlet red open-wheel single-seater Grand Prix race car with the same "FORMULA ARCHIVES" lettering and no other text, no F1 logo, no Formula 1 logo or wordmark, no FIA logo, no watermark in any corner, no sponsor logos, no numbers, one continuous camera move with no cuts. Glowing white airflow lines rush straight through the open slot of the rear wing. Then the entire rear wing assembly breaks apart into a technical exploded view: the endplates, the main plane, the upper flap, the hydraulic actuator, the piston, gears and every bolt and screw separate and float apart in mid-air above the fast-moving blurred asphalt, each part clearly visible and spinning slowly. Finally all the parts fly back together and lock into place, and the complete rear wing closes its flap. Crisp mechanical macro detail. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: the same confident British male documentary narrator with a deep, warm, clear voice, speaking at a steady pace, says in English: "The open slot lets air slip straight through, cutting drag, so the car gains around ten to twelve kilometres per hour on the straight."',
    ],
    "motorsport_legend_innovation": [
        'Photorealistic 3D CGI, premium motorsport engineering documentary style, one continuous camera move with no cuts. A late-1970s wedge-shaped Grand Prix race car with slick tyres and a large rear wing in a deep glossy scarlet red with white and black accents livery with the name "FORMULA ZONE" in large clean white block capital letters on both sidepods, no other text, no sponsor logos, no numbers, no F1 logo, no Formula 1 logo or wordmark, no FIA logo, no watermark in any corner, races across a historic asphalt Grand Prix circuit seen from a dramatic chase tracking angle. The camera swoops close to the rear engine bay and floor. The bodywork becomes translucent in a technical X-ray cutaway revealing an underbody suction fan mechanism: a central driven hub, curved carbon-fibre fan blades, and perimeter rubber sealing skirts hugging the asphalt. The fan spins at high speed, pulling atmospheric air from beneath the underfloor and venting it rearward. Macro mechanical fidelity, motion blur across the track surface, no exhaust flames or fireballs, no F1 logo, no sponsor logos, no watermarks. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: a calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "In nineteen seventy-eight, engineers fitted an engine-driven fan at the rear, generating immense suction under the chassis regardless of speed."',
        'Continue seamlessly from this exact frame, same photorealistic 3D CGI engineering documentary style, same scarlet red late-1970s wedge-shaped Grand Prix race car with slick tyres and a large rear wing with the same "FORMULA ZONE" lettering and no other text, no F1 logo, no Formula 1 logo or wordmark, no FIA logo, no watermark in any corner, no sponsor logos, no numbers, no exhaust flames or fireballs, one continuous camera move with no cuts. Glowing blue low-pressure suction vectors pull the chassis down towards the track surface as high-speed turbulent airflow streams rearward. The rear engine bay and floor assembly lifts and breaks apart into a technical exploded view in mid-air: the central driven fan hub, high-pitch composite rotor blades, protective mesh cowl, drive clutch, and sealed underbody ducts separate and float with crisp mechanical precision above the rushing asphalt. The components smoothly reassemble and lock into place, finishing on a caption-free freeze moment of the operational device. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: the same calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "The enormous ground effect produced unbelievable cornering grip, but rival protests over thrown debris led to an immediate regulatory ban."',
    ],
    "motorsport_corner_masterclass": [
        'Photorealistic 3D CGI, cinematic broadcast motorsport analysis style, one continuous camera move with no cuts. A modern open-wheel single-seater Grand Prix race car in a deep glossy scarlet red with white and black accents livery with the name "FORMULA ZONE" in large clean white block capital letters on both sidepods, no other text, no sponsor logos, no numbers, no F1 logo, no Formula 1 logo or wordmark, no FIA logo, no watermark in any corner races toward a generic unnamed heavy-braking hairpin at over three hundred kilometres per hour, captured in a dynamic drone chase shot. A glowing neon racing line illuminates the asphalt track ahead, with braking-point marker boards rendered as plain blank geometric rectangular foam boards without text or numbers. Carbon-ceramic brake discs glow bright cherry red under heavy deceleration while the driver initiates turn-in. No F1 logo, no sponsor logos, no watermarks. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: a calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "Approaching a heavy braking hairpin, the driver stomps the pedal with over one hundred kilograms of force, scrubbing speed in hundred metres."',
        'Continue seamlessly from this exact frame, same cinematic broadcast motorsport analysis style, same scarlet red open-wheel single-seater Grand Prix race car with the same "FORMULA ZONE" lettering and no other text, no F1 logo, no Formula 1 logo or wordmark, no FIA logo, no watermark in any corner, no sponsor logos, no numbers, one continuous camera move with no cuts. The camera swings low into the geometric apex of the heavy-braking hairpin, displaying glowing blue lateral force vectors and glowing yellow speed arrows across the chassis. Extreme macro close-up captures tyre sidewall deformation gripping the red-and-white rumble kerb before the car unleashes full traction and accelerates onto the exit straight. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: the same calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "At the apex, lateral force reaches five Gs as tyres deform over the kerb before full throttle propels the car forward."',
    ],
}


@pytest.mark.parametrize("key", ["motorsport_tech_explainer", "motorsport_legend_innovation", "motorsport_corner_masterclass"])
def test_example_prompts_exact_match(key):
    t = video_templates.get_template(key)
    vars = dict(t.raw["example"]["variables"])
    if "vehicle_preset" not in vars:
        vars["vehicle_preset"] = "f1"
    clips = video_templates.render_clips(t, vars, 2)
    rendered = [c["prompt"] for c in clips]
    assert rendered == EXPECTED_EXAMPLE_PROMPTS[key]


@pytest.mark.parametrize("key", ["motorsport_legend_innovation", "motorsport_corner_masterclass"])
def test_no_denylist_terms_in_template_json(key):
    t = video_templates.get_template(key)
    raw_text = str(t.raw)
    for forbidden in DENYLIST:
        pattern = re.compile(rf"\b{re.escape(forbidden)}\b", re.IGNORECASE)
        match = pattern.search(raw_text)
        assert match is None, f"Forbidden term '{forbidden}' found in template {key}"


def test_required_variables_custom_and_preset():
    t_innov = video_templates.get_template("motorsport_legend_innovation")
    req_innov = video_templates.required_variables(t_innov, 2)
    assert "vehicle_preset" in req_innov
    assert "era_vehicle" in req_innov
    assert "innovation_name" in req_innov
    assert "innovation_part" in req_innov
    assert "narration_1" in req_innov
    assert "narration_2" in req_innov

    t_corner = video_templates.get_template("motorsport_corner_masterclass")
    req_corner = video_templates.required_variables(t_corner, 2)
    assert "vehicle_preset" in req_corner
    assert "corner_type" in req_corner
    assert "approach_speed" in req_corner
    assert "narration_1" in req_corner
    assert "narration_2" in req_corner


@pytest.mark.parametrize("key", ["motorsport_legend_innovation", "motorsport_corner_masterclass"])
def test_render_clips_for_new_templates(key):
    t = video_templates.get_template(key)
    example_vars = t.raw["example"]["variables"]
    assert example_vars["vehicle_preset"] in ("f1", "classic_gp")
    assert example_vars["channel_name"] == "FORMULA ZONE"
    assert example_vars["narrator_preset"] == "EN-SCOT"
    assert "livery" in example_vars

    clips = video_templates.render_clips(t, example_vars, 2)
    assert len(clips) == 2

    for clip in clips:
        prompt = clip["prompt"]
        assert "{" not in prompt and "}" not in prompt
        assert "no F1 logo" in prompt or "no Formula 1 logo" in prompt
        assert "Voice-over:" in prompt

    assert clips[1]["prompt"].startswith("Continue seamlessly from this exact frame")
    assert "one continuous camera move with no cuts" in clips[1]["prompt"]


@pytest.mark.parametrize("key", ["motorsport_legend_innovation", "motorsport_corner_masterclass"])
def test_topic_ideas_count_and_format(key):
    t = video_templates.get_template(key)
    ideas = t.raw.get("topic_ideas", [])
    if isinstance(ideas, dict):
        total_ideas = sum(len(v) for v in ideas.values())
        assert total_ideas >= 10
    else:
        assert isinstance(ideas, list)
        assert len(ideas) >= 10
