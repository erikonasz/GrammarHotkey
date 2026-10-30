"""Real requests in several languages and modes, to judge quality by eye. Uses your Claude quota."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from grammarhotkey import claude_cli as c, config, guard  # noqa: E402

cfg = config.load()
modes = {m.id: m for m in config.default_modes()}
GRAMMAR = [
    "ich habe gestern mit mein freund gesprochen und er sagte das er nicht kommen kann",
    "je suis allez au magasin hier et j'ai acheter du pain pour ma mere",
    "ayer fui a la tienda y compre pan para mi madre porque no tenia nada en casa",
    "wczoraj poszedlem do sklepu i kupilem chleb dla mojej mamy bo nie miala",
    "вчера я пошол в магазин и купил хлеб для моей мамы потомучто у ней не было",
    "labas, sorry bet i cant come rytoj nes turiu daug darbo, gal poryt?",
    "lol nx blet vel sugedo tas kompas 😂 reik naujo pirkt",
]
OTHER = [("professional", "sveiki, noriu paklausti ar jau gavot mano saskaita nes dar nesumokejot, parasykit"),
         ("rewrite", "the thing is that i was thinking maybe we could like do the meeting later if thats ok")]
runner = c.ClaudeRunner()
for mode_id, text in [("grammar", t) for t in GRAMMAR] + OTHER:
    mode = modes[mode_id]
    start = time.perf_counter()
    answer = runner.run(mode.prompt, text, cfg)
    checked = guard.check(answer.text, text, mode.strict)
    print(f"\n[{mode_id}] {time.perf_counter() - start:.1f} s guard_ok={checked.ok} {checked.reason}")
    print("  in :", text)
    print("  out:", checked.text)
