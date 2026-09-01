import math
import uuid

ANNOTATION_COLORS={"cyan","blue","violet","amber","rose","green","slate"}


def validate_topology_annotations(value):
    if not isinstance(value,list): raise ValueError("Annotations must be a list")
    if len(value)>200: raise ValueError("A topology can contain at most 200 annotations")
    normalized=[];identities=set()
    for index,item in enumerate(value,1):
        if not isinstance(item,dict): raise ValueError(f"Annotation {index} must be an object")
        try: identity=str(uuid.UUID(str(item.get("id"))))
        except (TypeError,ValueError,AttributeError): raise ValueError(f"Annotation {index} needs a valid UUID")
        if identity in identities: raise ValueError("Annotation IDs must be unique")
        identities.add(identity);kind=item.get("type")
        if kind not in ("note","region"): raise ValueError(f"Annotation {index} has an unsupported type")
        geometry={}
        for field,minimum,maximum in (("x",-10000,10000),("y",-10000,10000),("width",80,2000),("height",40,1600)):
            number=item.get(field)
            if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or number<minimum or number>maximum:
                raise ValueError(f"Annotation {index} has an invalid {field}")
            geometry[field]=round(float(number),2)
        text=item.get("text","")
        if not isinstance(text,str) or not text.strip() or len(text.encode("utf-8"))>2000:
            raise ValueError(f"Annotation {index} text must be between 1 and 2000 UTF-8 bytes")
        color=item.get("color","cyan")
        if color not in ANNOTATION_COLORS: raise ValueError(f"Annotation {index} has an invalid color")
        font_size=item.get("fontSize",14);z_index=item.get("zIndex",0)
        if isinstance(font_size,bool) or not isinstance(font_size,int) or not 10<=font_size<=32:
            raise ValueError(f"Annotation {index} has an invalid font size")
        if isinstance(z_index,bool) or not isinstance(z_index,int) or not -100<=z_index<=100:
            raise ValueError(f"Annotation {index} has an invalid layer")
        normalized.append({"id":identity,"type":kind,**geometry,"text":text.strip(),"color":color,
            "fontSize":font_size,"zIndex":z_index})
    return normalized


def normalize_legacy_topology_annotations(value,owner_id):
    """Upgrade trusted pre-canvas annotation records without weakening write validation."""
    if not isinstance(value,list): raise ValueError("Annotations must be a list")
    upgraded=[]
    for index,item in enumerate(value):
        if not isinstance(item,dict):
            upgraded.append(item);continue
        kind=item.get("type");identity=item.get("id")
        try: uuid.UUID(str(identity));valid_id=True
        except (TypeError,ValueError,AttributeError): valid_id=False
        legacy=kind in ("text","box","rectangle","area") or not valid_id
        if not legacy:
            upgraded.append(item);continue
        region=kind in ("box","rectangle","area")
        def bounded(field,default,minimum,maximum):
            value=item.get(field,default)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): value=default
            return max(minimum,min(maximum,float(value)))
        upgraded.append({
            "id":str(uuid.uuid5(uuid.NAMESPACE_URL,f"io.containerlab.studio:{owner_id}:annotation:{index}")),
            "type":"region" if region else "note",
            "x":bounded("x",0,-10000,10000),"y":bounded("y",0,-10000,10000),
            "width":bounded("width",360 if region else 240,80,2000),
            "height":bounded("height",200 if region else 90,40,1600),
            "text":str(item.get("text") or item.get("label") or ("Network region" if region else "Topology note"))[:2000],
            "color":item.get("color") if item.get("color") in ANNOTATION_COLORS else ("blue" if region else "cyan"),
            "fontSize":int(bounded("fontSize",16 if region else 14,10,32)),
            "zIndex":int(bounded("zIndex",-10 if region else 10,-100,100)),
        })
    return validate_topology_annotations(upgraded)
