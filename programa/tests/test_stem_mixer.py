import tempfile
from pathlib import Path
import unittest
import numpy as np
import soundfile as sf
from stem_mixer import active_stems, mix_preview


class MixerTests(unittest.TestCase):
    def test_solo_and_mute(self):
        files=[Path('piano.wav'),Path('accordion.wav')]
        settings={'piano.wav':{'solo':True,'volume':50},'accordion.wav':{'volume':100}}
        self.assertEqual(active_stems(files,settings),[(files[0],.5)])
        settings['piano.wav']['mute']=True
        self.assertFalse(active_stems(files,settings))

    def test_real_audio_mixed_and_soloed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); t=np.arange(44100)/44100
            first=root/'piano.wav'; second=root/'accordion.wav'
            sf.write(first,.1*np.sin(2*np.pi*440*t),44100)
            sf.write(second,.1*np.sin(2*np.pi*880*t),44100)
            settings={str(first):{'volume':100},str(second):{'volume':100}}
            mixed=mix_preview([first,second],settings,root/'mix')
            settings[str(first)]['solo']=True
            solo=mix_preview([first,second],settings,root/'mix')
            def strength(file,hz):
                data,rate=sf.read(file); mono=data.mean(axis=1)
                spectrum=abs(np.fft.rfft(mono)); index=round(hz*len(mono)/rate)
                return spectrum[max(0,index-2):index+3].max()
            self.assertGreater(strength(mixed,880),10)
            self.assertLess(strength(solo,880),strength(solo,440)*.02)
            with self.assertRaises(ValueError):mix_preview([],{},root/'mix')
