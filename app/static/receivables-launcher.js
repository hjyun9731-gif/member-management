(()=>{
  if(document.getElementById('receivablesModuleLauncher')) return;
  const a=document.createElement('a');
  a.id='receivablesModuleLauncher'; a.href='/receivables'; a.textContent='수납/미수금'; a.title='수납·미수금 통합관리';
  const host=document.querySelector('[data-role="category-menu"],#category-menu,.category-menu,#categoryTabs,.category-tabs,.top-tabs');
  if(host){
    Object.assign(a.style,{display:'inline-flex',alignItems:'center',justifyContent:'center',height:'38px',padding:'0 14px',marginLeft:'8px',borderRadius:'12px',background:'#e3edff',color:'#3558aa',fontWeight:'800',textDecoration:'none',border:'1px solid #d7e3fb',whiteSpace:'nowrap'});
    host.appendChild(a);
  }else{
    Object.assign(a.style,{position:'fixed',right:'22px',bottom:'22px',zIndex:'9999',padding:'11px 16px',borderRadius:'999px',background:'#e3edff',color:'#3558aa',fontWeight:'800',textDecoration:'none',boxShadow:'0 8px 26px rgba(48,57,76,.12)',border:'1px solid #d7e3fb'});
    document.body.appendChild(a);
  }
})();
