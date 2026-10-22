from app.services.topic_similarity import similar_subjects, subject_of, subject_tokens


def test_subject_of():
    assert subject_of("The turbocharger: why turbo lag is back in 2026") == "The turbocharger"
    assert subject_of("The tyre squash that creates the grip in a corner") == "The tyre squash that creates the grip in a corner"
    assert subject_of("  Active aero: flap movements  ") == "Active aero"


def test_subject_tokens():
    tokens = subject_tokens("The tear-off visor strips: the driver peeling transparent layers…")
    assert tokens == {"tear", "off", "visor", "strip"}

    tokens_tyre = subject_tokens("The wet tyre: …")
    assert tokens_tyre == {"wet", "tyre"}

    tokens_squash = subject_tokens("The tyre squash that creates the grip in a corner")
    assert tokens_squash == {"tyre", "squash", "that", "create", "grip", "corner"}

    tokens_aero = subject_tokens("2026 active aero: the rear-wing flap that pivots between Corner and Straight mode")
    assert tokens_aero == {"active", "aero"}


def test_real_examples_similar():
    # Pair 1: tear-off strips
    a1 = "The tear-off visor strips: the driver peeling transparent layers…"
    b1 = "The tear-off strip: one pull and the visor is crystal clear"
    assert similar_subjects(a1, b1)

    # Pair 2: clutch bite point
    a2 = "The clutch bite point: the paddle release that launches the car"
    b2 = "The clutch bite point: how the driver finds the launch threshold"
    assert similar_subjects(a2, b2)

    # Pair 3: 2026 active aero
    a3 = "2026 active aero: the rear-wing flap that pivots between Corner and Straight mode"
    b3 = "2026 active aero: why the rear wing flaps adjust on the straight"
    assert similar_subjects(a3, b3)

    # Pair 4: turbo / turbocharger
    a4 = "The turbocharger: why turbo lag is back in 2026"
    b4 = "The turbo: exhaust gases spin its turbine wheel over 125,000 rpm"
    assert similar_subjects(a4, b4)


def test_dissimilar_examples():
    # Brake bias dial vs butterfly throttle
    a = "The brake bias dial: fine-tuning front to rear stopping balance"
    b = "The butterfly throttle: how the intake valve regulates airflow"
    assert not similar_subjects(a, b)

    # Wet tyre vs tyre squash (1/2 = 0.5 < 0.6)
    c = "The wet tyre: tread patterns dispersing water at speed"
    d = "The tyre squash that creates the grip in a corner"
    assert not similar_subjects(c, d)


def test_empty_and_short_edge_cases():
    assert not similar_subjects("", "")
    assert not similar_subjects("f1 2026", "f1 2026")
    assert not similar_subjects("A: foo", "B: bar")
