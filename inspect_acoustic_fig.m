% Read the supplied figure; export its geometry and plotting metadata locally.
f = openfig('D:\LIFU\2026_LIFU\3D_acoustic_field\acoustic_field_500 kHz_focal.fig','invisible');
cleanup = onCleanup(@() delete(f(isgraphics(f))));
patches = findall(f,'Type','patch');
surfaces = struct('vertices',{},'faces',{},'face_color',{},'face_alpha',{},'display_name',{},'cdata',{});
for k=1:numel(patches)
    p=patches(k);
    surfaces(k).vertices = get(p,'Vertices');
    surfaces(k).faces = get(p,'Faces');
    surfaces(k).face_color = get(p,'FaceColor');
    surfaces(k).face_alpha = get(p,'FaceAlpha');
    surfaces(k).display_name = get(p,'DisplayName');
    surfaces(k).cdata = get(p,'FaceVertexCData');
end
axesObjects=findall(f,'Type','axes');
metadata=struct();
metadata.patches=numel(patches);
metadata.axes=struct('xlabel',{},'ylabel',{},'zlabel',{},'xlim',{},'ylim',{},'zlim',{},'clim',{});
for k=1:numel(axesObjects)
    a=axesObjects(k);
    metadata.axes(k).xlabel=get(get(a,'XLabel'),'String');
    metadata.axes(k).ylabel=get(get(a,'YLabel'),'String');
    metadata.axes(k).zlabel=get(get(a,'ZLabel'),'String');
    metadata.axes(k).xlim=get(a,'XLim');
    metadata.axes(k).ylim=get(a,'YLim');
    metadata.axes(k).zlim=get(a,'ZLim');
    metadata.axes(k).clim=get(a,'CLim');
end
legends=findall(f,'Type','legend');
metadata.legends=cell(numel(legends),1);
for k=1:numel(legends)
    metadata.legends{k}=get(legends(k),'String');
end
save('diagnostics/acoustic_fig_export.mat','surfaces','metadata','-v7');
fid=fopen('diagnostics/acoustic_fig_metadata.json','w');
fprintf(fid,'%s',jsonencode(metadata));
fclose(fid);
exportgraphics(f,'diagnostics/acoustic_fig_preview.png','Resolution',130);
disp(jsonencode(metadata));
