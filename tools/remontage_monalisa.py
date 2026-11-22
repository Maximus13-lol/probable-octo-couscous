import concurrent.futures, io, json, math, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT=Path.cwd()
WORK=ROOT/"work"
OUT=WORK/"monalisa-new-cut"
OUT.mkdir(exist_ok=True)
SIZE=(1280,720)
FPS=25
FONT=ROOT/"assets/fonts/FiraSansCondensed-ExtraBold.ttf"
MEDIA=WORK/"monalisa-archive"
ARCHIVE={x["id"]:x for x in json.loads((MEDIA/"sources.json").read_text(encoding="utf-8"))}
ARCHIVE[0]={"image":str(ROOT/"assets/archive-pilot/wall.jpg"),"caption":"Пустое место в Квадратном салоне, тысяча девятьсот одиннадцатый год",
            "source":"https://commons.wikimedia.org/wiki/File:Mona_Lisa_stolen-1911.jpg",
            "license":"Public domain","author":"Неизвестный автор"}
DARK="#111722"
YELLOW="#ffd200"

def ff(*args):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-nostdin","-y",*map(str,args)],check=True)

def probe(path):
    return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(path)],text=True).strip())

def label_fit(draw,text,size,limit):
    while size>18:
        font=ImageFont.truetype(str(FONT),size)
        if draw.textlength(text,font=font)<limit:
            return font
        size-=1
    return ImageFont.truetype(str(FONT),size)

def layers(item,section,index):
    with Image.open(item["image"]) as raw:
        photo=ImageOps.exif_transpose(raw).convert("RGB")
    backdrop=ImageEnhance.Brightness(ImageOps.fit(photo,SIZE).filter(ImageFilter.GaussianBlur(32))).enhance(.29)
    draw=ImageDraw.Draw(backdrop)
    draw.rectangle((0,655,1280,720),fill=DARK)
    draw.rectangle((44,675,50,703),fill=YELLOW)
    draw.text((65,691),item["caption"],font=label_fit(draw,item["caption"],27,1120),fill="white",anchor="lm")
    section_label=f"ПАПКА №13  /  {section}"
    draw.text((52,37),section_label,font=label_fit(draw,section_label,24,960),fill=YELLOW,anchor="lm")
    bg=OUT/f"bg-{index:03d}.jpg"; backdrop.save(bg,quality=91)
    plate=Image.new("RGBA",SIZE)
    if index%3==1:
        photo=ImageOps.fit(photo,(1160,557),centering=(.5,.35))
    else:
        photo=ImageOps.contain(photo,(1120,550))
    x=(1280-photo.width)//2
    y=76+(550-photo.height)//2
    pd=ImageDraw.Draw(plate)
    pd.rectangle((x-3,y-3,x+photo.width+3,y+photo.height+3),fill="#aaa28a")
    plate.paste(photo,(x,y))
    fg=OUT/f"fg-{index:03d}.png";plate.save(fg)
    return bg,fg

def shot(spec):
    i,section,key,frames=spec
    bg,fg=layers(ARCHIVE[key],section,i)
    direction=1 if i%2 else -1
    target=OUT/f"shot-{i:03d}.mp4"
    filter=(f"[0:v]scale=1344:756,crop=1280:720:x='32-{direction}*10*sin(t/7)':y=18[b];"
            f"[b][1:v]overlay=x='{direction}*22*sin(t/7)':y='4*sin(t/9)'[v]")
    ff("-filter_complex_threads","1","-loop","1","-framerate",FPS,"-i",bg,
       "-loop","1","-framerate",FPS,"-i",fg,"-filter_complex",filter,"-map","[v]",
       "-frames:v",frames,"-an","-c:v","libx264","-threads","2","-preset","veryfast",
       "-crf","21","-pix_fmt","yuv420p",target)
    return target

def main_track():
    source=ROOT/"outputs/monalisa-music.mp4"
    duration=probe(source)
    sections=[
        ("ПРОПАЖА",.17,[0,17,18,21,2]),
        ("ПОРТРЕТ",.25,[1,2,21,17]),
        ("ПАРИЖ И ПРЕССА",.58,[19,20,4,0,5,6,18]),
        ("ПЕРУДЖА И ФЛОРЕНЦИЯ",.78,[5,6,8,10,11,4]),
        ("ВОЗВРАЩЕНИЕ",1.0,[13,15,16,12,8,11,1]),
    ]
    start=0
    specs=[]
    for section,end,keys in sections:
        stop=round(duration*end*FPS)
        count=math.ceil((stop-start)/(15*FPS))
        for index in range(count):
            left=start+(stop-start)*index//count
            right=start+(stop-start)*(index+1)//count
            specs.append((len(specs),section,keys[index%len(keys)],right-left))
        start=stop
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        shots=list(executor.map(shot,specs))
    listing=OUT/"visuals.txt"
    listing.write_text("".join(f"file '{path.name}'\n" for path in shots))
    ff("-f","concat","-safe","1","-i",listing,"-i",source,"-map","0:v:0","-map","1:a:0",
       "-c:v","copy","-c:a","aac","-ar","48000","-ac","2","-b:a","192k","-shortest",OUT/"main.mp4")
    print(f"{len(specs)} движущихся планов, {len(set(k for _,_,k,_ in specs))} архивных материалов",flush=True)

def finish(greeting):
    intro=ROOT/"assets/long-intro.mp4"
    teaser=ROOT/"outputs/monalisa-preview-fira.jpg"
    for idx,image in [(1,teaser),(2,Path(ARCHIVE[0]["image"]))]:
        audio=greeting/f"scene_{idx:02d}.wav"
        ff("-loop","1","-framerate",FPS,"-i",image,"-i",audio,
           "-vf","scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
           "-map","0:v:0","-map","1:a:0","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",
           "-ar","48000","-ac","2","-b:a","192k","-shortest",OUT/f"opening-{idx}.mp4")
    ff("-i",intro,"-map","0:v:0","-map","0:a:0","-vf","fps=25","-c:v","libx264","-crf","19",
       "-pix_fmt","yuv420p","-af","volume=0.8","-c:a","aac","-ar","48000","-ac","2","-b:a","192k",OUT/"intro.mp4")
    listing=OUT/"final.txt"; listing.write_text("".join(f"file '{file}'\n" for file in ("opening-1.mp4","intro.mp4","opening-2.mp4","main.mp4")))
    ff("-f","concat","-safe","1","-i",listing,"-c","copy","-movflags","+faststart",ROOT/"outputs/monalisa-new-format.mp4")
    print(probe(ROOT/"outputs/monalisa-new-format.mp4"))

if __name__=="__main__":
    if len(sys.argv)==1:
        main_track()
    else:
        finish(Path(sys.argv[1]))
