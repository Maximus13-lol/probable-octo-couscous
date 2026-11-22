import array, math, wave
from pathlib import Path
sr=22050
tracks=[
 ("archive", [(48,55,60,63),(44,51,56,60),(51,58,63,67),(46,53,58,62)], 8, .13),
 ("investigation", [(45,52,57,60),(41,48,53,57),(48,55,60,64),(43,50,55,59)], 6, .19),
 ("discovery", [(48,55,60,64),(43,50,55,59),(45,52,57,60),(41,48,53,57)], 7, .16),
 ("epilogue", [(48,55,60,64),(45,52,57,60),(41,48,53,57),(43,50,55,62)], 9, .12),
]
tau=math.tau
for name,chords,bar,pluck in tracks:
    samples=array.array('h')
    for chord in chords:
        freq=[tau*440*2**((n-69)/12) for n in chord]
        for i in range(sr*bar):
            t=i/sr
            env=min(1,t/.8,(bar-t)/1.2)
            bed=sum(math.sin(f*t)*v for f,v in zip(freq,(.29,.18,.12,.09)))
            beat=bar/4
            hit=int(t/beat)
            local=t-hit*beat
            f=freq[(0,2,1,3)[hit]]*2
            tone=(math.sin(f*local)+.22*math.sin(2*f*local))*min(1,local/.027)*math.exp(-3.6*local)
            samples.append(int(21800*(env*bed+pluck*tone)*min(1,(bar-t)/.15)))
    with wave.open(str(Path("work") / f"monalisa-{name}.wav"),"wb") as out:
        out.setparams((1,2,sr,0,"NONE","not compressed"))
        out.writeframes(samples.tobytes())
