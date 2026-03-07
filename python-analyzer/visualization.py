import config

# Centralized theme helper for charts

def apply_theme(chart):
    """Apply themes for a consistent look throughout charts."""
    chart.update_layout(
        height=config.VIS_CONFIG['chart_height'],
        hovermode='closest',
        font=dict(size=12),
        margin=dict(l=20, r=20, t=20, b=20),
        title_font=dict(size=16),
        xaxis=dict(showgrid=True, gridcolor='LightGray'),
        yaxis=dict(showgrid=True, gridcolor='LightGray')
    )
    return chart

# Example of how to use this in a chart

def create_chart(data):
    chart = ...  # Create your chart object here
    apply_theme(chart)
    return chart
