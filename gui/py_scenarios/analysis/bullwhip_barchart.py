import plotly.graph_objects as go

# Bullwhip magnification factor per scenario: CV(most upstream tier) /
# CV(customer-facing tier), as printed by each scenario's standalone run.

normal2 = sum([3.5053076427641403, 3.3045595959721825, 3.522523468162061, 3.6723504899051824, 3.982691932439607, 3.97514750104804, 3.7330753627147364, 3.678245349618678, 3.4032325294441583, 3.506766862004367, 3.457505133481609])/12
ltbp2 = sum([2.9756861194029423, 3.6284579936125905, 2.869123286030586, 3.6408260005241035, 2.7302226236537877, 3.6047140434395146, 2.3677833612473598, 3.071429456952861, 2.4212586158122402, 2.8236582784725477])/12
normal5 = sum([4.008478151507757, 4.96091257785753, 4.6153954972184845, 4.98799330037111, 3.7657462005520173, 4.379993462755655, 2.688834456962304, 4.029922736373254, 4.0435977255797235, 3.7910019064348894])/12

scenarios = [
    ("1x1x1 Normal", 3.8371),
    ("2x2x2 Normal", normal2),
    ("2x2x2 LTBP", ltbp2),
    ("5x5x5 Normal", 4.6183),
    ("5x5x5 LTBP", 3.3),
]

names = [name for name, _ in scenarios]
heights = [height for _, height in scenarios]

# Three colours, deliberately distinct from the qualitative.Plotly palette the
# line graphs use: grey for the 1x1x1 baseline, red for the other Normal runs,
# green for the LTBP runs.
GREY, RED, GREEN = "#7F7F7F", "#C0392B", "#27AE60"

def colour(name):
    if "LTBP" in name:
        return GREEN
    if name.startswith("1x1x1"):
        return GREY
    return RED

colours = [colour(name) for name in names]

fig = go.Figure(go.Bar(x=names, y=heights, text=heights, textposition="outside",
                       marker_color=colours))
fig.update_xaxes(title_text="Scenario", tickangle=-45)
fig.update_yaxes(title_text="Bullwhip magnification factor")
fig.update_layout(
    template="plotly_white", title=None, width=500, height=500)
fig.show()
