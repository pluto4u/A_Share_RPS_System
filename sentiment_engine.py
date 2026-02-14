def final_position(score_position, trend_gate, breadth_cap):
    return min(score_position, trend_gate, breadth_cap)


def score_to_position(score):
    if score >= 80:
        return 80
    if score >= 60:
        return 60
    if score >= 40:
        return 30
    if score >= 20:
        return 10
    return 0


def apply_breadth_cap(position, breadth_pct, freeze_threshold=10.0):
    if breadth_pct < freeze_threshold:
        return min(position, 10)
    return position
