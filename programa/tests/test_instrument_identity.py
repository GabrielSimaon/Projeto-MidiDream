import os
import sys
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from instrument_detection import decide_presence, verify_identity


class IdentityTests(unittest.TestCase):
    def test_strong_accordion_does_not_become_piano(self):
        presence = decide_presence([{'Accordion':.9, 'Piano':.1}])
        self.assertFalse(verify_identity('piano', presence)['accepted'])
        self.assertTrue(verify_identity('accordion', presence)['accepted'])

    def test_different_instruments_may_coexist(self):
        presence = decide_presence([{'Accordion':.8,'Piano':.8,'Acoustic guitar':.75,'Electric guitar':.75}])
        for kind in presence: self.assertTrue(verify_identity(kind, presence)['accepted'])

    def test_uncertainty_does_not_silently_remove_audio(self):
        result = verify_identity('accordion', decide_presence([{}]))
        self.assertTrue(result['accepted'])
        self.assertTrue(result['uncertain'])

    def test_automatic_piano_stem_is_rechecked_and_extracted_as_accordion(self):
        import numpy as np
        import soundfile as sf
        import instrument_worker as worker
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); source=root/'source'; source.mkdir(); output=root/'out'
            audio=(.1*np.sin(np.arange(32000)*.05)).astype(np.float32)
            sf.write(source/'piano.wav', audio,32000)
            presence=decide_presence([{'Accordion':.9,'Piano':.01}])
            args=['worker','--source',str(source),'--output',str(output),'--models',str(root/'models'),'--verify-known']
            previous=os.getcwd()
            try:
                with patch('sys.argv',args), patch.object(worker,'detect',return_value=presence) as detect, patch.object(worker,'download_weights'), patch.object(worker,'load_backend',return_value=SimpleNamespace(build_audiosep=lambda *args:object())), patch.object(worker,'extract',return_value=audio*.8):
                    worker.main()
                    self.assertEqual(detect.call_count,2)
                data=json.loads((output/'instrumentos.json').read_text())
                self.assertTrue((output/'accordion.wav').is_file())
                self.assertFalse((output/'piano.wav').exists())
                self.assertTrue(data['automatic_stems_rechecked'])
            finally: os.chdir(previous)

    def test_same_extracted_audio_cannot_fill_two_instrument_roles(self):
        import numpy as np
        from instrument_detection import duplicate_source
        t=np.arange(32000)/32000
        piano=np.sin(2*np.pi*440*t)
        self.assertEqual(duplicate_source(piano*.5, {'piano':piano}), 'piano')
        self.assertIsNone(duplicate_source(np.sin(2*np.pi*880*t), {'piano':piano}))
