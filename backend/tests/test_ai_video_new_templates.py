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
        'Photorealistic 3D CGI, premium motorsport engineering documentary style, one continuous camera move with no cuts. A modern open-wheel single-seater Grand Prix race car in a deep glossy red with white accents on the nose, sidepods and rear wing, a black floor and a black halo livery with a small, subtle "FORMULA ARCHIVES" name in clean white letters on the side of the engine cover, no other text, no sponsor logos, no numbers, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, speeds forward along a sunny circuit seen from a high chase angle, the asphalt streaming backward beneath it. The camera flies forward and down into an extreme close-up of the rear wing. The rear wing bodywork turns semi-transparent like an X-ray cutaway, explicitly revealing the DRS actuator inside the central support pillar: a slim hydraulic cylinder whose piston is linked to the upper flap. The piston pushes and the upper flap of the rear wing visibly rotates upward, opening a wide slot between the two wing elements. Only the upper rear-wing flap moves; the lower main plane, both vertical endplates and the central support pillar stay completely fixed, rigid and level, and nothing else bends, tilts or warps. Crisp mechanical macro detail, motion blur on the asphalt below. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: a confident British male documentary narrator with a deep, warm, clear voice, speaking at a steady pace, says in English: "This is DRS, the Drag Reduction System. Inside the rear wing, a small hydraulic actuator pushes a piston to tilt the upper flap open."',
        'Continue seamlessly from this exact frame, same photorealistic 3D CGI engineering documentary style, same glossy red open-wheel single-seater Grand Prix race car with the same small "FORMULA ARCHIVES" name and no other text, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, no sponsor logos, no numbers, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, one continuous camera move with no cuts. The car keeps driving forward at the same speed as in the previous shot and never appears to roll or drive backwards; the wheels and the asphalt always move the way they do for a car travelling forward. The camera may change angle like a professional motorsport camera (tracking, orbiting or crane moves), but every change of angle is one smooth, visible move, never a jump. Glowing white airflow lines rush straight through the open slot of the rear wing. Then the entire rear wing assembly breaks apart into a technical exploded view: the endplates, the main plane, the upper flap, the hydraulic actuator, the piston, gears separate and float apart in mid-air above the fast-moving blurred asphalt, each part clearly visible and spinning slowly. Finally all the parts fly back together and lock into place, and the complete rear wing closes its flap. Crisp mechanical macro detail. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: the same confident British male documentary narrator with a deep, warm, clear voice, speaking at a steady pace, says in English: "The open slot lets air slip straight through, cutting drag, so the car gains around ten to twelve kilometres per hour on the straight."',
    ],
    "motorsport_legend_innovation": [
        'Photorealistic 3D CGI, premium motorsport engineering documentary style, one continuous camera move with no cuts. A late-1970s wedge-shaped Grand Prix race car with slick tyres and a large rear wing in a deep glossy red with white accents on the nose, sidepods and rear wing, a black floor and a black halo livery with a small, subtle "FORMULA ZONE" name in clean white letters on the side of the engine cover, no other text, no sponsor logos, no numbers, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, races across a historic asphalt Grand Prix circuit seen from a dramatic chase tracking angle. The camera swoops close to the rear engine bay and floor. The bodywork becomes translucent in a technical X-ray cutaway revealing an underbody suction fan mechanism: a central driven hub, curved carbon-fibre fan blades, and perimeter rubber sealing skirts hugging the asphalt. The fan spins at high speed, pulling atmospheric air from beneath the underfloor and venting it rearward. Macro mechanical fidelity, motion blur across the track surface, no exhaust flames or fireballs, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no sponsor logos, no watermarks. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: a calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "In nineteen seventy-eight, engineers fitted an engine-driven fan at the rear, generating immense suction under the chassis regardless of speed."',
        'Continue seamlessly from this exact frame, same photorealistic 3D CGI engineering documentary style, same glossy red late-1970s wedge-shaped Grand Prix race car with slick tyres and a large rear wing with the same small "FORMULA ZONE" name and no other text, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, no sponsor logos, no numbers, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, no exhaust flames or fireballs, one continuous camera move with no cuts. Glowing blue low-pressure suction vectors pull the chassis down towards the track surface as high-speed turbulent airflow streams rearward. The rear engine bay and floor assembly lifts and breaks apart into a technical exploded view in mid-air: the central driven fan hub, high-pitch composite rotor blades, protective mesh cowl, drive clutch, and sealed underbody ducts separate and float with crisp mechanical precision above the rushing asphalt. The components smoothly reassemble and lock into place, finishing on a caption-free freeze moment of the operational device. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: the same calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "The enormous ground effect produced unbelievable cornering grip, but rival protests over thrown debris led to an immediate regulatory ban."',
    ],
    "motorsport_corner_masterclass": [
        'Photorealistic 3D CGI, cinematic broadcast motorsport analysis style, one continuous camera move with no cuts. A modern open-wheel single-seater Grand Prix race car in a deep glossy red with white accents on the nose, sidepods and rear wing, a black floor and a black halo livery with a small, subtle "FORMULA ZONE" name in clean white letters on the side of the engine cover, no other text, no sponsor logos, no numbers, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner races toward a generic unnamed heavy-braking hairpin at over three hundred kilometres per hour, captured in a dynamic drone chase shot. A glowing neon racing line illuminates the asphalt track ahead, with braking-point marker boards rendered as plain blank geometric rectangular foam boards without text or numbers. Carbon-ceramic brake discs glow bright cherry red under heavy deceleration while the driver initiates turn-in. No official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no sponsor logos, no watermarks. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: a calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "Approaching a heavy braking hairpin, the driver stomps the pedal with over one hundred kilograms of force, scrubbing speed in hundred metres."',
        'Continue seamlessly from this exact frame, same cinematic broadcast motorsport analysis style, same glossy red open-wheel single-seater Grand Prix race car with the same small "FORMULA ZONE" name and no other text, no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos), no watermark in any corner, no sponsor logos, no numbers, one continuous camera move with no cuts. The camera swings low into the geometric apex of the heavy-braking hairpin, displaying glowing blue lateral force vectors and glowing yellow speed arrows across the chassis. Extreme macro close-up captures tyre sidewall deformation gripping the red-and-white rumble kerb before the car unleashes full traction and accelerates onto the exit straight. Audio: engine and wind ambience quietly under the voice-over, no music. Voice-over: the same calm Scottish man in his fifties with a gentle rasp, speaking conversationally in English at an easy pace with tiny natural hesitations, like an old mechanic telling a story, not a polished announcer, says: "At the apex, lateral force reaches five Gs as tyres deform over the kerb before full throttle propels the car forward."',
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
    phrase = "no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos)"
    assert len(rendered) == len(EXPECTED_EXAMPLE_PROMPTS[key])
    for prompt in rendered:
        assert phrase in prompt
        assert "no sponsor logos" in prompt
        assert "one continuous camera move" in prompt


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
        assert "no official league, series or governing-body logos (no F1, FIA, MotoGP, FIM or Dorna logos)" in prompt
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
