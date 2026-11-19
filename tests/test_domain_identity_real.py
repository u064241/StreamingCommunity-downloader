"""Identity of the real site's start page, shaped like the live answer seen on
2026-11-19: component "Home/Home" and sliders trending, latest, top10. Only
structure is modelled here, no real content."""
import pytest

from app.core import page


def _real_shape(component="Home/Home", sliders=("trending", "latest", "top10")):
    return {
        "component": component,
        "version": "synthetic-version",
        "url": "/",
        "props": {
            "title": "synthetic",
            "sliders": [{"name": name, "titles": []} for name in sliders],
            "genres": [],
        },
    }


def test_the_real_home_shape_is_recognised():
    assert page.looks_like_home(_real_shape()) is True


@pytest.mark.parametrize("component", ["Home", "Home/Home"])
def test_both_known_home_component_names_are_accepted(component):
    assert page.looks_like_home(_real_shape(component=component)) is True


@pytest.mark.parametrize("component", ["Search/Search", "Title/Show", "Home/Other", "", None])
def test_other_components_are_refused(component):
    assert page.looks_like_home(_real_shape(component=component)) is False


def test_a_home_without_the_known_sliders_is_refused():
    assert page.looks_like_home(_real_shape(sliders=("something-else",))) is False


def test_a_page_without_props_is_refused():
    assert page.looks_like_home({"component": "Home/Home"}) is False
