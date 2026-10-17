from eval.baselines import DoNothing, NaiveBigBang, StaticRules

POLICIES = {"do_nothing": DoNothing, "big_bang": NaiveBigBang, "static": StaticRules}

__all__ = ["POLICIES", "DoNothing", "NaiveBigBang", "StaticRules"]
